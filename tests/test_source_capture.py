"""Unit tests for capture_url (docs/TASKS_CITATION_VERIFICATION.md T4).

No real network — every test builds an httpx.Client backed by httpx.MockTransport (design
decision-mandated: real fetches only ever happen from the worker, T5, never from a request or a
test). `sleep` is always a no-op fake here, so the per-domain 1s pacing (design decision 9) never
actually slows the suite down.
"""

import hashlib
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
    """`_robots_cache` is module-level, process-lifetime state by design (production is a

    long-lived worker) — but that means it leaks between tests unless cleared, so a robots.txt
    rule from one test can't silently affect another. The per-domain throttle itself moved to the
    database (`app/services/rate_limit.py`, code-review finding 2026-09-30) — `db_session`'s own
    per-test rollback/isolation already takes care of that, nothing to clear here.
    """
    source_capture._robots_cache.clear()
    yield
    source_capture._robots_cache.clear()


def _client(handler) -> httpx.Client:
    """The REAL production client (honest UA, no automatic redirects, timeout) over a mock transport."""
    return source_capture.build_capture_client(transport=httpx.MockTransport(handler))


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


# --- redirects hop by hop (docs/TASKS_CITATION_HARDENING.md T1) -------------------------------

GATEWAY = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
GATEWAY_ROBOTS = b"User-agent: *\nDisallow: /grounding-api-redirect\n"
HTML_OK = b"<html><body><p>Real content.</p></body></html>"


def _gateway_handler(target_robots: bytes | None = None, *, log: list[str] | None = None):
    """Gateway (robots.txt: Disallow) -> https://real-source.example/article."""

    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "vertexaisearch.cloud.google.com":
            if request.url.path == "/robots.txt":
                return httpx.Response(200, content=GATEWAY_ROBOTS)
            return httpx.Response(302, headers={"location": "https://real-source.example/article"})
        if request.url.path == "/robots.txt":
            if target_robots is None:
                return httpx.Response(404)
            return httpx.Response(200, content=target_robots)
        return httpx.Response(200, content=HTML_OK, headers={"content-type": "text/html"})

    return handler


def test_gateway_with_disallow_resolves_to_allowed_target(db_session: Session):
    log: list[str] = []
    doc = capture_url(
        db_session, GATEWAY, now=NOW, client=_client(_gateway_handler(log=log)), sleep=_no_sleep
    )

    assert doc.error_reason is None
    assert doc.method == "live"
    assert doc.requested_url == GATEWAY
    assert doc.final_url == "https://real-source.example/article"
    assert "vertexaisearch.cloud.google.com/robots.txt" not in log  # gateway robots never read


def test_gateway_to_target_with_disallow_is_robots(db_session: Session):
    client = _client(_gateway_handler(b"User-agent: *\nDisallow: /article\n"))
    doc = capture_url(db_session, GATEWAY, now=NOW, client=client, sleep=_no_sleep)

    assert doc.error_reason == "robots"
    assert doc.requested_url == GATEWAY
    assert doc.final_url == "https://real-source.example/article"  # the hop that blocked


def test_redirect_to_other_domain_checks_that_domains_robots(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "old.example":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(301, headers={"location": "https://new.example/page"})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /\n")
        return httpx.Response(200, content=HTML_OK)

    doc = capture_url(db_session, "https://old.example/page", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "robots"
    assert doc.final_url == "https://new.example/page"


def test_first_hop_robots_block_leaves_final_url_empty(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"User-agent: *\nDisallow: /\n")

    doc = capture_url(db_session, "https://example.com/x", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "robots"
    assert doc.final_url is None


def test_relative_location_is_resolved_against_current_hop(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/moved/here"})
        return httpx.Response(200, content=HTML_OK, headers={"content-type": "text/html"})

    doc = capture_url(db_session, "https://example.com/start", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason is None
    assert doc.final_url == "https://example.com/moved/here"


def test_redirect_loop_hits_limit_and_records_last_hop(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(302, headers={"location": "/loop"})

    doc = capture_url(db_session, "https://example.com/loop", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "timeout"  # same bucket as the old TooManyRedirects
    assert doc.text_sha256 is None


def test_redirect_to_unsupported_scheme_is_a_failure_not_a_crash(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(302, headers={"location": "ftp://example.com/file"})

    doc = capture_url(db_session, "https://example.com/go", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "timeout"


def test_timeout_at_target_records_target_as_final_url(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "vertexaisearch.cloud.google.com":
            return httpx.Response(302, headers={"location": "https://slow.example/a"})
        raise httpx.ReadTimeout("slow", request=request)

    doc = capture_url(db_session, GATEWAY, now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "timeout"
    assert doc.final_url == "https://slow.example/a"


def test_gateway_that_does_not_redirect_records_its_status(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"expired")

    doc = capture_url(db_session, GATEWAY, now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "http_404"
    assert doc.http_status == 404
    assert doc.bytes == 0


def test_throttle_paces_the_gateway_briefly_and_the_target_normally(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """The gateway hop is paced (docs/TASKS_CITATION_HARDENING.md T10 — unpaced, a batch of Gemini
    citations hammers it) but with its own short interval; the target page keeps the normal one
    under ITS host, so all Gemini citations still do not share one 1 s slot.
    """
    throttled: list[tuple[str, float | None]] = []
    monkeypatch.setattr(
        source_capture,
        "_throttle_domain",
        lambda db, domain, *, sleep, interval_seconds=None: throttled.append((domain, interval_seconds)),
    )

    capture_url(db_session, GATEWAY, now=NOW, client=_client(_gateway_handler()), sleep=_no_sleep)

    assert throttled == [
        ("vertexaisearch.cloud.google.com", source_capture.GATEWAY_MIN_INTERVAL_SECONDS),
        ("real-source.example", None),  # None = the normal per-domain interval
    ]


def test_two_gateway_citations_in_a_row_wait_for_the_gateway_slot(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """The real DB-backed throttle, not a stub: the second citation's gateway request arrives
    within milliseconds of the first and must be made to wait out (most of) the short interval.
    """
    monkeypatch.setattr(source_capture, "MIN_DOMAIN_INTERVAL_SECONDS", 0.0)  # isolate the gateway
    # A long interval, so the assertion does not depend on how quickly the second call arrives: with
    # the real 0.2 s a slow machine could let the slot expire in between and no wait would be needed.
    monkeypatch.setattr(source_capture, "GATEWAY_MIN_INTERVAL_SECONDS", 5.0)
    slept: list[float] = []

    for suffix in ("a", "b"):
        capture_url(
            db_session, f"{GATEWAY}-{suffix}", now=NOW, client=_client(_gateway_handler()), sleep=slept.append
        )

    gateway_waits = [seconds for seconds in slept if seconds > 0]
    assert len(gateway_waits) == 1
    assert 0 < gateway_waits[0] <= 5.0


def test_robots_txt_that_redirects_is_followed(db_session: Session):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if request.url.host == "example.com":
                return httpx.Response(301, headers={"location": "https://www.example.com/robots.txt"})
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, content=HTML_OK)

    doc = capture_url(db_session, "https://example.com/private", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason == "robots"


# --- NUL bytes (docs/TASKS_CITATION_HARDENING.md T4) -------------------------------------------


def test_capture_url_survives_a_nul_byte_in_the_page(db_session: Session):
    """The pilot failure: a NUL in a source's text failed the `source_texts` insert and, with it,
    the whole verification job. The capture must succeed and store the text without it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        robots = _allow_robots(request)
        if robots is not None:
            return robots
        return httpx.Response(
            200, content=b"<html><body><p>Vor\x00der Text.</p></body></html>", headers={"content-type": "text/html"}
        )

    doc = capture_url(db_session, "https://example.com/nul", now=NOW, client=_client(handler), sleep=_no_sleep)

    assert doc.error_reason is None
    stored = db_session.get(SourceText, doc.text_sha256)
    assert stored.text == "Vorder Text."
    assert "\x00" not in stored.text
    assert stored.chars == len("Vorder Text.")


def test_store_strips_a_nul_that_reaches_it_and_hashes_the_stored_text(db_session: Session):
    """Safety net: even a caller that bypasses extraction cannot get a NUL into the database, and
    `text_sha256` always describes exactly the text that was stored.
    """
    doc = source_capture._store(db_session, requested_url="https://example.com/direct", now=NOW, text="a\x00b")

    stored = db_session.get(SourceText, doc.text_sha256)
    assert stored.text == "ab"
    assert doc.text_sha256 == hashlib.sha256(b"ab").hexdigest()


# --- the production client itself (docs/TASKS_CITATION_HARDENING.md T10) ----------------------


def test_build_capture_client_settings():
    """It never follows redirects on its own (capture_url walks them hop by hop so robots.txt is
    checked per host) — which is exactly why every OTHER caller sharing it (archive.org lookups)
    must ask for redirects per request. Pinned here so changing either side is a conscious act.
    """
    client = source_capture.build_capture_client()

    assert client.follow_redirects is False
    assert client.headers["user-agent"] == source_capture.USER_AGENT
    assert client.timeout.read == source_capture.REQUEST_TIMEOUT_SECONDS
