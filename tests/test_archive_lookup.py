"""Tests for app/services/archive_lookup.py and its wiring into citation_verification.py /

verification_queue.py (docs/TASKS_CITATION_VERIFICATION.md T10, design decision 19).

No real network — every test builds an httpx.Client backed by httpx.MockTransport, same
convention as tests/test_source_capture.py and tests/test_verification_queue.py. `sleep` is
always a no-op fake for the higher-level tests; the throttle itself gets two small direct tests
of its own further down.
"""

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIModel, Citation, Prompt, RawResponse, Run
from app.models.verification import CitationVerification, SourceDocument, VerificationJob
from app.services import archive_lookup, source_capture
from app.services.archive_lookup import ArchiveSnapshot, ArchiveUnavailable, fetch_snapshot_content, find_closest_snapshot
from app.services.citation_verification import verify_citations_by_quote
from app.services.verification_queue import DEFAULT_LEASE_MINUTES, claim_next_job, enqueue_capture, process_verification_job

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
TARGET_DATE = datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)


def _no_sleep(_seconds: float) -> None:
    pass


@pytest.fixture(autouse=True)
def _reset_module_state():
    """`_last_request_at` is module-level, process-lifetime state by design (production is a

    long-lived worker) — cleared between tests the same way tests/test_source_capture.py resets
    source_capture's own module-level caches, so one test's throttling can't affect another's.
    Also clears source_capture's own caches: several tests here go through `process_verification_job`
    -> `capture_url` for the same citation URL, and without this, a leftover `_last_domain_request`
    entry from an earlier test could trigger a real (if short) `time.sleep` here too.
    """
    archive_lookup._last_request_at = None
    source_capture._last_domain_request.clear()
    source_capture._robots_cache.clear()
    yield
    archive_lookup._last_request_at = None
    source_capture._last_domain_request.clear()
    source_capture._robots_cache.clear()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


_CDX_ROW = [
    "de,karriere-familienunternehmen)/firmenprofile/knauf",
    "20260303091500",
    "https://www.karriere-familienunternehmen.de/firmenprofile/knauf",
    "text/html",
    "200",
    "ABCDEFG1234567",
    "12345",
]
_CDX_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


# --- find_closest_snapshot ----------------------------------------------------------------------


def test_find_closest_snapshot_returns_the_matching_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cdx/search/cdx"
        assert "closest=20260305090000" in str(request.url)
        assert "filter=statuscode%3A200" in str(request.url)
        return httpx.Response(200, json=[_CDX_HEADER, _CDX_ROW])

    snapshot = find_closest_snapshot(
        _client(handler), "https://www.karriere-familienunternehmen.de/firmenprofile/knauf",
        target_date=TARGET_DATE, sleep=_no_sleep,
    )

    assert snapshot == ArchiveSnapshot(
        archive_timestamp="20260303091500",
        fetch_url="https://web.archive.org/web/20260303091500id_/https://www.karriere-familienunternehmen.de/firmenprofile/knauf",
        view_url="https://web.archive.org/web/20260303091500/https://www.karriere-familienunternehmen.de/firmenprofile/knauf",
    )


def test_find_closest_snapshot_returns_none_for_an_empty_cdx_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert find_closest_snapshot(_client(handler), "https://example.com/gone", target_date=TARGET_DATE, sleep=_no_sleep) is None


def test_find_closest_snapshot_returns_none_for_header_only_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_CDX_HEADER])

    assert find_closest_snapshot(_client(handler), "https://example.com/gone", target_date=TARGET_DATE, sleep=_no_sleep) is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_find_closest_snapshot_raises_archive_unavailable_on_rate_limit_or_server_error(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(ArchiveUnavailable):
        find_closest_snapshot(_client(handler), "https://example.com/x", target_date=TARGET_DATE, sleep=_no_sleep)


def test_find_closest_snapshot_raises_archive_unavailable_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(ArchiveUnavailable):
        find_closest_snapshot(_client(handler), "https://example.com/x", target_date=TARGET_DATE, sleep=_no_sleep)


def test_find_closest_snapshot_raises_archive_unavailable_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ArchiveUnavailable):
        find_closest_snapshot(_client(handler), "https://example.com/x", target_date=TARGET_DATE, sleep=_no_sleep)


# --- fetch_snapshot_content ----------------------------------------------------------------------


def test_fetch_snapshot_content_extracts_html_text():
    snapshot = ArchiveSnapshot(
        archive_timestamp="20260303091500",
        fetch_url="https://web.archive.org/web/20260303091500id_/https://example.com/a",
        view_url="https://web.archive.org/web/20260303091500/https://example.com/a",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == snapshot.fetch_url
        return httpx.Response(
            200, content=b"<html><body><p>Knauf setzt auf nachhaltiges Bauen.</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    content = fetch_snapshot_content(_client(handler), snapshot, sleep=_no_sleep)

    assert content is not None
    assert "Knauf setzt auf nachhaltiges Bauen." in content.text
    assert content.page_starts is None


def test_fetch_snapshot_content_returns_none_when_extracted_text_is_empty():
    snapshot = ArchiveSnapshot(archive_timestamp="20260303091500", fetch_url="https://web.archive.org/web/x/id_/y", view_url="https://web.archive.org/web/x/y")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html><body><script>var x=1;</script></body></html>", headers={"content-type": "text/html"})

    assert fetch_snapshot_content(_client(handler), snapshot, sleep=_no_sleep) is None


@pytest.mark.parametrize("status", [429, 502])
def test_fetch_snapshot_content_raises_archive_unavailable_on_rate_limit_or_server_error(status):
    snapshot = ArchiveSnapshot(archive_timestamp="20260303091500", fetch_url="https://web.archive.org/web/x/id_/y", view_url="https://web.archive.org/web/x/y")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(ArchiveUnavailable):
        fetch_snapshot_content(_client(handler), snapshot, sleep=_no_sleep)


# --- _throttle -------------------------------------------------------------------------------


def test_throttle_sleeps_for_the_remaining_interval():
    archive_lookup._last_request_at = time.monotonic() - 1.0
    calls: list[float] = []

    archive_lookup._throttle(sleep=calls.append)

    assert len(calls) == 1
    assert 1.8 <= calls[0] <= 2.2


def test_throttle_does_not_sleep_once_the_interval_has_already_elapsed():
    archive_lookup._last_request_at = time.monotonic() - 10.0
    calls: list[float] = []

    archive_lookup._throttle(sleep=calls.append)

    assert calls == []


# --- integration: citation_verification.py's archive fallback ----------------------------------


def _anthropic_model(seed: dict) -> AIModel:
    return seed["anthropic_model"]


def _make_run_and_response(db_session: Session, seed: dict, sample_prompt: Prompt, *, rendered_text: str, raw_payload: dict) -> RawResponse:
    run = Run(
        prompt_id=sample_prompt.id, model_id=_anthropic_model(seed).id, market_id=seed["market"].id,
        persona_id=seed["persona"].id, trigger_type="manual", status="success", started_at=TARGET_DATE,
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload=raw_payload, rendered_text=rendered_text, has_citations=True)
    db_session.add(raw)
    db_session.commit()
    db_session.refresh(raw)
    return raw


def _add_citation(db_session: Session, raw: RawResponse, *, source_url: str, source_passage: str) -> Citation:
    citation = Citation(
        raw_response_id=raw.id, source_url=source_url, source_domain="karriere-familienunternehmen.de",
        citation_position=0, source_passage=source_passage,
    )
    db_session.add(citation)
    db_session.commit()
    db_session.refresh(citation)
    return citation


def _add_source_document(db_session: Session, *, url: str, error_reason: str | None = None, text: str | None = None) -> SourceDocument:
    import hashlib

    from app.models.verification import SourceText

    text_sha256 = None
    if text is not None:
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        db_session.add(SourceText(sha256=text_sha256, text=text, chars=len(text)))
    document = SourceDocument(
        requested_url=url, method="live", http_status=200 if text is not None else None,
        error_reason=error_reason, text_sha256=text_sha256, fetched_at=NOW, verifier_version="1.0",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


_RAW_PAYLOAD = {
    "content": [
        {
            "type": "text",
            "text": "Knauf ist ein familiengeführtes Unternehmen mit langer Tradition.",
            "citations": [{"url": "https://www.karriere-familienunternehmen.de/firmenprofile/knauf", "title": "Karriere", "cited_text": "..."}],
        }
    ]
}
_QUOTE = "Knauf ist seit Generationen ein familiengeführtes Unternehmen mit tiefer Verwurzelung."


def _archive_client(cdx_response: httpx.Response | Exception, snapshot_response: httpx.Response | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/cdx/search/cdx" in str(request.url):
            if isinstance(cdx_response, Exception):
                raise cdx_response
            return cdx_response
        assert snapshot_response is not None
        return snapshot_response

    return _client(handler)


def test_archive_fallback_upgrades_a_404_to_archive_only(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    _add_source_document(db_session, url=citation.source_url, error_reason="http_404")

    client = _archive_client(
        httpx.Response(200, json=[_CDX_HEADER, _CDX_ROW]),
        httpx.Response(200, content=f"<html><body><p>{_QUOTE}</p></body></html>".encode("utf-8"), headers={"content-type": "text/html"}),
    )

    verify_citations_by_quote(db_session, raw, now=NOW, client=client, sleep=_no_sleep)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "archive_only"
    assert verification.reason is None
    archive_document = db_session.get(SourceDocument, verification.source_document_id)
    assert archive_document.method == "archive"
    assert archive_document.archive_timestamp == "20260303091500"
    assert archive_document.final_url == "https://web.archive.org/web/20260303091500/https://www.karriere-familienunternehmen.de/firmenprofile/knauf"
    assert archive_document.requested_url == citation.source_url


def test_archive_fallback_upgrades_a_410_to_archive_only(db_session: Session, seed, sample_prompt: Prompt):
    """Confirms migration 0038 actually fixed the gap it was written for — before it, this would

    raise an IntegrityError the moment a live 410 (a legal value on source_documents.error_reason
    since T4, just never a legal citation_verifications.reason) tried to flow through this path.
    """
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    _add_source_document(db_session, url=citation.source_url, error_reason="http_410")

    client = _archive_client(
        httpx.Response(200, json=[_CDX_HEADER, _CDX_ROW]),
        httpx.Response(200, content=f"<html><body><p>{_QUOTE}</p></body></html>".encode("utf-8"), headers={"content-type": "text/html"}),
    )

    verify_citations_by_quote(db_session, raw, now=NOW, client=client, sleep=_no_sleep)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "archive_only"


def test_archive_fallback_leaves_unverifiable_when_cdx_has_no_snapshot(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    _add_source_document(db_session, url=citation.source_url, error_reason="http_404")

    client = _archive_client(httpx.Response(200, json=[]))

    verify_citations_by_quote(db_session, raw, now=NOW, client=client, sleep=_no_sleep)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "unverifiable"
    assert verification.reason == "http_404"


def test_archive_fallback_upgrades_a_live_not_found_to_page_changed(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    # Live page reachable (no error_reason) but its current text has nothing matching the quote.
    _add_source_document(db_session, url=citation.source_url, text="Diese Seite wurde komplett neu gestaltet und zeigt jetzt andere Inhalte.")

    client = _archive_client(
        httpx.Response(200, json=[_CDX_HEADER, _CDX_ROW]),
        httpx.Response(200, content=f"<html><body><p>{_QUOTE}</p></body></html>".encode("utf-8"), headers={"content-type": "text/html"}),
    )

    verify_citations_by_quote(db_session, raw, now=NOW, client=client, sleep=_no_sleep)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "page_changed"


def test_archive_fallback_leaves_not_found_when_archive_also_lacks_the_quote(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    _add_source_document(db_session, url=citation.source_url, text="Ganz anderer Inhalt ohne jeden Bezug.")

    client = _archive_client(
        httpx.Response(200, json=[_CDX_HEADER, _CDX_ROW]),
        httpx.Response(200, content=b"<html><body><p>Auch der Archivschnappschuss hat einen komplett anderen Text.</p></body></html>", headers={"content-type": "text/html"}),
    )

    verify_citations_by_quote(db_session, raw, now=NOW, client=client, sleep=_no_sleep)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "not_found"


def test_no_archive_fallback_attempted_without_a_client(db_session: Session, seed, sample_prompt: Prompt):
    """`client=None` (the default) must behave exactly like before T10 — no archive.org traffic

    at all, even for a citation that would otherwise trigger a fallback.
    """
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)
    _add_source_document(db_session, url=citation.source_url, error_reason="http_404")

    verify_citations_by_quote(db_session, raw, now=NOW)  # no client passed

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "unverifiable"
    assert verification.reason == "http_404"


# --- integration: a rate-limited archive.org defers the whole verification job -----------------


def test_rate_limited_archive_lookup_defers_the_whole_job(db_session: Session, seed, sample_prompt: Prompt):
    """docs/TASKS_CITATION_VERIFICATION.md T10 point 3 — "429 -> úloha odložená": ArchiveUnavailable

    propagates out of verify_citations_by_quote, through process_verification_job's own
    unexpected-exception handling (unchanged from T5), and defers the job with backoff — never
    recorded as any citation's verdict.
    """
    raw = _make_run_and_response(db_session, seed, sample_prompt, rendered_text="...", raw_payload=_RAW_PAYLOAD)
    citation = _add_citation(db_session, raw, source_url="https://www.karriere-familienunternehmen.de/firmenprofile/knauf", source_passage=_QUOTE)

    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)
    assert job.attempts == 1

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if "/cdx/search/cdx" in str(request.url):
            return httpx.Response(429)
        return httpx.Response(404)  # the live capture itself: page is gone, triggering the archive lookup

    process_verification_job(db_session, job, now=NOW, client=_client(handler))

    assert job.status == "deferred"
    assert job.scheduled_for == NOW + timedelta(minutes=1)
    assert db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id)) is None
