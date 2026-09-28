"""Unit tests for capture_url (docs/TASKS_CITATION_VERIFICATION.md T4).

No real network — every test builds an httpx.Client backed by httpx.MockTransport (design
decision-mandated: real fetches only ever happen from the worker, T5, never from a request or a
test). `sleep` is always a no-op fake here, so the per-domain 1s pacing (design decision 9) never
actually slows the suite down.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.verification import SourceDocument, SourceText
from app.services import source_capture
from app.services.source_capture import capture_url

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sources"
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


def _text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _no_sleep(_seconds: float) -> None:
    pass


@pytest.fixture(autouse=True)
def _reset_module_state():
    """These caches are module-level, process-lifetime state by design (production is a

    long-lived worker) — but that means they leak between tests unless cleared, so a robots.txt
    rule or a domain's last-request timestamp from one test can't silently affect another.
    """
    source_capture._robots_cache.clear()
    source_capture._last_domain_request.clear()
    yield
    source_capture._robots_cache.clear()
    source_capture._last_domain_request.clear()


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": source_capture.USER_AGENT},
        follow_redirects=True,
    )


def _allow_robots(request: httpx.Request) -> httpx.Response | None:
    """Returns a 404 for any /robots.txt request (= allowed) so tests that don't care about

    robots.txt don't each need to handle it — return None for any other request, letting the
    caller's own handler take over.
    """
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return None


def test_capture_url_stores_success_and_extracted_text(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=_text("simple.html").encode("utf-8"), headers={"content-type": "text/html; charset=utf-8"})

    doc = capture_url(
        db_session, "https://example.com/article", now=NOW, client=_client(handler), sleep=_no_sleep
    )

    assert doc.error_reason is None
    assert doc.http_status == 200
    assert doc.method == "live"
    assert doc.final_url == "https://example.com/article"
    assert doc.verifier_version == source_capture.VERIFIER_VERSION
    assert doc.text_sha256 is not None
    assert "Leichtbau in BW" in db_session.get(SourceText, doc.text_sha256).text

    stored = db_session.scalar(select(SourceDocument).where(SourceDocument.id == doc.id))
    assert stored is not None


def test_capture_url_detects_200_radware_interstitial_as_bot_challenge(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=_text("radware.html").encode("utf-8"), headers={"content-type": "text/html"})

    doc = capture_url(db_session, "https://example.com/blocked", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "bot_challenge"
    assert doc.challenge_vendor == "radware"
    assert doc.text_sha256 is None


def test_capture_url_403_with_cf_mitigated_header_is_bot_challenge(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(403, content=b"forbidden", headers={"cf-mitigated": "challenge"})

    doc = capture_url(db_session, "https://example.com/knauf-page", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "bot_challenge"
    assert doc.challenge_vendor == "cloudflare"
    assert doc.http_status == 403


def test_capture_url_404_is_recorded_without_challenge(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(404, content=b"not found")

    doc = capture_url(db_session, "https://example.com/gone", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "http_404"
    assert doc.http_status == 404
    assert doc.challenge_vendor is None


def test_capture_url_extracts_pdf_and_records_page_starts(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=_bytes("sample.pdf"), headers={"content-type": "application/pdf"})

    doc = capture_url(db_session, "https://example.com/report.pdf", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason is None
    assert len(doc.page_starts) == 2
    assert doc.page_starts[0] == 0
    stored_text = db_session.get(SourceText, doc.text_sha256).text
    assert "Technologien" in stored_text
    assert stored_text[doc.page_starts[1] :].startswith("Zweite Seite Inhalt.")


def test_capture_url_pdf_without_text_layer_is_pdf_no_text(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=_bytes("blank.pdf"), headers={"content-type": "application/pdf"})

    doc = capture_url(db_session, "https://example.com/scan.pdf", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "pdf_no_text"
    assert doc.text_sha256 is None


def test_capture_url_robots_disallowed_never_fetches_the_page(db_session: Session):
    fetched_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched_paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, content=b"should never be requested")

    doc = capture_url(
        db_session, "https://example.com/private/secret", now=NOW, client=_client(handler), sleep=_no_sleep
    )

    assert doc.error_reason == "robots"
    assert fetched_paths == ["/robots.txt"]


def test_capture_url_follows_redirect_and_records_final_url(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        if request.url.path == "/grounding-api-redirect/abc":
            return httpx.Response(302, headers={"location": "https://real-source.example/article"})
        return httpx.Response(200, content=b"<html><body><p>Real content.</p></body></html>", headers={"content-type": "text/html"})

    doc = capture_url(
        db_session,
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
        now=NOW,
        client=_client(handler),
        sleep=_no_sleep,
    )

    assert doc.requested_url == "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    assert doc.final_url == "https://real-source.example/article"
    assert doc.error_reason is None


def test_capture_url_uses_cached_document_within_24h(db_session: Session):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"<html><body><p>Fresh content.</p></body></html>", headers={"content-type": "text/html"})

    client = _client(handler)
    first = capture_url(db_session, "https://example.com/cached", now=NOW, client=client, sleep=_no_sleep)
    calls_after_first = calls

    second = capture_url(
        db_session, "https://example.com/cached", now=NOW + timedelta(hours=1), client=client, sleep=_no_sleep
    )

    assert second.id == first.id
    assert calls == calls_after_first  # no new HTTP request at all, not even robots.txt


def test_capture_url_cache_expires_after_24h(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=b"<html><body><p>Content.</p></body></html>", headers={"content-type": "text/html"})

    client = _client(handler)
    first = capture_url(db_session, "https://example.com/stale", now=NOW, client=client, sleep=_no_sleep)
    second = capture_url(
        db_session, "https://example.com/stale", now=NOW + timedelta(hours=25), client=client, sleep=_no_sleep
    )

    assert second.id != first.id


def test_capture_url_dedupes_identical_text_across_different_urls(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(200, content=b"<html><body><p>Identical content.</p></body></html>", headers={"content-type": "text/html"})

    client = _client(handler)
    first = capture_url(db_session, "https://a.example.com/page", now=NOW, client=client, sleep=_no_sleep)
    second = capture_url(db_session, "https://b.example.com/page", now=NOW, client=client, sleep=_no_sleep)

    assert first.id != second.id
    assert first.text_sha256 == second.text_sha256
    assert db_session.query(SourceText).count() == 1
    assert db_session.query(SourceDocument).count() == 2


def test_capture_url_too_large_by_content_length_header(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-type": "text/html", "content-length": str(source_capture.MAX_BYTES + 1)},
        )

    doc = capture_url(db_session, "https://example.com/huge", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "too_large"


def test_capture_url_connection_error_is_bucketed_as_timeout(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        raise httpx.ConnectError("connection refused", request=request)

    doc = capture_url(db_session, "https://unreachable.example/page", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "timeout"
