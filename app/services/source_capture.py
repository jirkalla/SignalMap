"""Fetch, classify, and extract one cited source URL (docs/TASKS_CITATION_VERIFICATION.md T4,

design decisions 6-13). The one entry point, `capture_url`, always returns a `SourceDocument` —
success and failure are both recorded as evidence (a new row every time, NFR-6), never raised as
an exception the caller has to separately handle.

Never called from a request (design decision 3) — this is worker-only (app/services/
verification_queue.py, T5). `client` is always supplied by the caller rather than built inside
this module: production code shares one long-lived `build_capture_client()` client across many
calls (connection reuse), and tests inject an `httpx.Client` backed by `httpx.MockTransport` —
this module never makes a real network call on its own.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.verification import SourceDocument, SourceText
from app.services.source_extract import extract_html, extract_pdf

logger = logging.getLogger(__name__)

# Bumped whenever capture or extraction logic changes meaningfully — recorded on every
# SourceDocument row so a later re-capture can tell which verifier version produced which
# snapshot (design decision 2: a better extractor adds a new row, never edits the old one).
VERIFIER_VERSION = "1.0"

# design decision 7 — a real, reachable URL a site owner can use to ask questions about this
# crawler, not a generic placeholder. https://expressyourself.ai is SignalMap's own production
# domain (docs/DEPLOYMENT.md).
USER_AGENT = "SignalMapVerifier/1.0 (+https://expressyourself.ai)"

CACHE_WINDOW = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 10
MAX_BYTES = 20 * 1024 * 1024

# design decision 9's "1 s mezi požadavky na stejnou doménu" — the "nejvýš 1 požadavek na
# doménu" half of that decision is NOT enforced here: verification_jobs are claimed and
# processed one at a time (design decision 3, T5), so at most one capture_url call is ever
# in flight at all from this process, for any domain, without this module needing a lock of
# its own. Only the minimum spacing between consecutive requests to the SAME domain — which
# serial processing does not give you for free when many citations share one domain — is
# this module's job.
MIN_DOMAIN_INTERVAL_SECONDS = 1.0

_last_domain_request: dict[str, float] = {}
_robots_cache: dict[str, RobotFileParser] = {}

# Short body (design decision 8: "< 1500 znaků") matching a known bot-protection interstitial,
# even on a plain HTTP 200 — the whole point being that status code alone is not sufficient
# evidence of real content.
_CHALLENGE_BODY_MAX_CHARS = 1500
_CHALLENGE_PATTERN = re.compile(
    r"verifying your browser|just a moment|checking your browser|captcha|access denied"
    r"|incapsula|radware|cloudflare|akamai",
    re.IGNORECASE,
)


def _vendor_from_keyword(keyword: str) -> str:
    """Map one matched interstitial keyword to a vendor name for `challenge_vendor`."""
    keyword = keyword.lower()
    if "radware" in keyword:
        return "radware"
    if "incapsula" in keyword:
        return "incapsula"
    if "akamai" in keyword:
        return "akamai"
    return "cloudflare"  # cloudflare, "just a moment", "checking your browser" all point here


def build_capture_client() -> httpx.Client:
    """The one real `httpx.Client` production capture uses — honest UA, timeout, and redirect

    cap (design decisions 6/7/9) all live here, in one place, so nothing calling `capture_url`
    can accidentally construct a client that skips them.
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
    )


def _throttle_domain(domain: str, *, sleep: Callable[[float], None]) -> None:
    """Block (via `sleep`) until at least `MIN_DOMAIN_INTERVAL_SECONDS` have passed since the

    last request to `domain` from this process. `sleep` is injected (defaults to `time.sleep`
    in `capture_url`) so tests can pass a no-op and never actually wait.
    """
    now = time.monotonic()
    last = _last_domain_request.get(domain)
    if last is not None:
        remaining = MIN_DOMAIN_INTERVAL_SECONDS - (now - last)
        if remaining > 0:
            sleep(remaining)
    _last_domain_request[domain] = time.monotonic()


def _robots_allowed(client: httpx.Client, url: str) -> bool:
    """Whether `USER_AGENT` may fetch `url`, per that domain's `robots.txt` (design decision 7).

    Cached per-origin for the life of the process. A missing or unreachable `robots.txt` means
    "allowed" — the standard convention, and the safe default (a transient failure to fetch the
    policy file must never itself block otherwise-legitimate capture).
    """
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    parser = _robots_cache.get(origin)
    if parser is None:
        parser = RobotFileParser()
        try:
            response = client.get(urljoin(origin, "/robots.txt"))
        except httpx.RequestError:
            response = None
        if response is not None and response.status_code < 400:
            parser.parse(response.text.splitlines())
        else:
            parser.allow_all = True  # no reachable robots.txt = allow (standard convention)
        # RobotFileParser.can_fetch() refuses everything as long as `last_checked` is falsy —
        # normally set by .read()'s own HTTP fetch, which we bypass to go through `client`
        # instead (honest UA, MockTransport-testable) — so it must be set here explicitly, or
        # every capture would be silently treated as robots-disallowed, .parse() notwithstanding.
        parser.last_checked = 1
        _robots_cache[origin] = parser
    return parser.can_fetch(USER_AGENT, url)


def _decode_text(body: bytes, content_type: str) -> str:
    """Decode a response body using the charset the server declared, falling back to UTF-8

    (replacing undecodable bytes rather than raising — a mis-declared or missing charset must
    never turn into a capture failure).
    """
    charset = None
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    if charset:
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("utf-8", errors="replace")


def _detect_text_challenge(body_text: str) -> tuple[str, str] | None:
    """`("bot_challenge", vendor)` when `body_text` looks like a bot-protection interstitial

    rather than real content (design decision 8), else None. Only ever called for a 200 response
    — a challenge page returned with a blocking status (403/503) is detected separately, from
    headers, in `capture_url`, before extraction is even attempted.
    """
    if len(body_text) >= _CHALLENGE_BODY_MAX_CHARS:
        return None
    match = _CHALLENGE_PATTERN.search(body_text)
    if match is None:
        return None
    return "bot_challenge", _vendor_from_keyword(match.group(0))


def _vendor_from_headers(headers: httpx.Headers) -> str | None:
    """Vendor guess from response headers alone, for a blocking 403/503 (design decision 8)."""
    if "cf-mitigated" in headers:
        return "cloudflare"
    if "cloudflare" in headers.get("server", "").lower():
        return "cloudflare"
    return None


@dataclass(frozen=True)
class _FetchResult:
    final_url: str
    status: int
    headers: httpx.Headers
    body: bytes
    duration_ms: int


class _TooLarge(Exception):
    """Raised internally when a response body exceeds `MAX_BYTES` — caught in `capture_url`,

    never escapes it. Not `httpx`'s own exception hierarchy, since this isn't a transport
    failure, it's this module's own policy limit.
    """


def _fetch(client: httpx.Client, url: str) -> _FetchResult:
    started = time.perf_counter()
    with client.stream("GET", url) as response:
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_BYTES:
            raise _TooLarge()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_BYTES:
                raise _TooLarge()
            chunks.append(chunk)
        return _FetchResult(
            final_url=str(response.url),
            status=response.status_code,
            headers=response.headers,
            body=b"".join(chunks),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _cached_document(db: Session, url: str, *, now: datetime) -> SourceDocument | None:
    """The most recent `method='live'` capture of `url`, if it's within `CACHE_WINDOW` of `now`

    (design decision 13) — `method='archive'` rows never satisfy this cache, since an archived
    snapshot answers a different question (T10) than "what does the live page say right now".
    """
    cutoff = now - CACHE_WINDOW
    return db.scalar(
        select(SourceDocument)
        .where(
            SourceDocument.requested_url == url,
            SourceDocument.method == "live",
            SourceDocument.fetched_at >= cutoff,
        )
        .order_by(SourceDocument.fetched_at.desc())
        .limit(1)
    )


def _store(
    db: Session,
    *,
    requested_url: str,
    now: datetime,
    final_url: str | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
    error_reason: str | None = None,
    challenge_vendor: str | None = None,
    text: str | None = None,
    page_starts: list[int] | None = None,
    locations: list[dict[str, Any]] | None = None,
    num_bytes: int | None = None,
    duration_ms: int | None = None,
) -> SourceDocument:
    """Insert one `source_documents` row — always a new row (NFR-6) — and, when `text` is given,

    insert-if-missing the matching `source_texts` row (design decision 12's content-addressed
    dedup: `ON CONFLICT (sha256) DO NOTHING`, since identical text needs storing only once).
    """
    text_sha256 = None
    if text is not None:
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        db.execute(
            pg_insert(SourceText)
            .values(sha256=text_sha256, text=text, chars=len(text))
            .on_conflict_do_nothing(index_elements=["sha256"])
        )

    document = SourceDocument(
        requested_url=requested_url,
        final_url=final_url,
        method="live",
        http_status=http_status,
        content_type=content_type,
        error_reason=error_reason,
        challenge_vendor=challenge_vendor,
        text_sha256=text_sha256,
        page_starts=page_starts,
        locations=locations,
        bytes=num_bytes,
        duration_ms=duration_ms,
        fetched_at=now,
        verifier_version=VERIFIER_VERSION,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def capture_url(
    db: Session,
    url: str,
    *,
    now: datetime,
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceDocument:
    """Fetch and extract `url`, returning a `SourceDocument` — always, whether it succeeded or

    not (design decisions 6-13). Order of operations: 24h cache, then `robots.txt`, then the
    per-domain pacing, then the fetch itself, then challenge detection, then extraction.

    `now` drives both the cache check and the row's own `fetched_at` (never `datetime.now()`
    internally) — deterministic and test-injectable, same convention as
    `app/services/run_execution.py`/`app/worker.py`. `sleep` likewise — see
    `MIN_DOMAIN_INTERVAL_SECONDS`.
    """
    cached = _cached_document(db, url, now=now)
    if cached is not None:
        return cached

    if not _robots_allowed(client, url):
        return _store(db, requested_url=url, now=now, error_reason="robots")

    domain = urlsplit(url).netloc
    _throttle_domain(domain, sleep=sleep)

    try:
        fetched = _fetch(client, url)
    except _TooLarge:
        return _store(db, requested_url=url, now=now, error_reason="too_large")
    except httpx.TimeoutException:
        return _store(db, requested_url=url, now=now, error_reason="timeout")
    except httpx.RequestError as exc:
        # No dedicated code for a generic connection failure (DNS, connection refused, too many
        # redirects, ...) in design decision 28's reason list — bucketed under "timeout" rather
        # than inventing a new one, since both mean the same thing to an analyst: this source
        # could not be reached this time.
        logger.info("capture_url: connection error for %s: %s", url, exc)
        return _store(db, requested_url=url, now=now, error_reason="timeout")

    content_type = fetched.headers.get("content-type", "")
    is_pdf = "pdf" in content_type.lower() or fetched.final_url.lower().split("?")[0].endswith(".pdf")

    if fetched.status in (403, 503):
        vendor = _vendor_from_headers(fetched.headers)
        if vendor is not None:
            return _store(
                db,
                requested_url=url,
                now=now,
                final_url=fetched.final_url,
                http_status=fetched.status,
                content_type=content_type,
                error_reason="bot_challenge",
                challenge_vendor=vendor,
                num_bytes=len(fetched.body),
                duration_ms=fetched.duration_ms,
            )
        reason = "http_403" if fetched.status == 403 else "http_5xx"
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=fetched.final_url,
            http_status=fetched.status,
            content_type=content_type,
            error_reason=reason,
            num_bytes=len(fetched.body),
            duration_ms=fetched.duration_ms,
        )

    if fetched.status >= 400:
        reason = "http_404" if fetched.status == 404 else ("http_5xx" if fetched.status >= 500 else f"http_{fetched.status}")
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=fetched.final_url,
            http_status=fetched.status,
            content_type=content_type,
            error_reason=reason,
            num_bytes=len(fetched.body),
            duration_ms=fetched.duration_ms,
        )

    if is_pdf:
        extracted_pdf = extract_pdf(fetched.body)
        if extracted_pdf is None:
            return _store(
                db,
                requested_url=url,
                now=now,
                final_url=fetched.final_url,
                http_status=fetched.status,
                content_type=content_type,
                error_reason="pdf_no_text",
                num_bytes=len(fetched.body),
                duration_ms=fetched.duration_ms,
            )
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=fetched.final_url,
            http_status=fetched.status,
            content_type=content_type,
            text=extracted_pdf.text,
            page_starts=extracted_pdf.page_starts,
            num_bytes=len(fetched.body),
            duration_ms=fetched.duration_ms,
        )

    body_text = _decode_text(fetched.body, content_type)
    challenge = _detect_text_challenge(body_text)
    if challenge is not None:
        error_reason, vendor = challenge
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=fetched.final_url,
            http_status=fetched.status,
            content_type=content_type,
            error_reason=error_reason,
            challenge_vendor=vendor,
            num_bytes=len(fetched.body),
            duration_ms=fetched.duration_ms,
        )

    extracted_html = extract_html(body_text)
    if not extracted_html.text.strip():
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=fetched.final_url,
            http_status=fetched.status,
            content_type=content_type,
            error_reason="no_checkable_text",
            num_bytes=len(fetched.body),
            duration_ms=fetched.duration_ms,
        )

    return _store(
        db,
        requested_url=url,
        now=now,
        final_url=fetched.final_url,
        http_status=fetched.status,
        content_type=content_type,
        text=extracted_html.text,
        locations=extracted_html.locations,
        num_bytes=len(fetched.body),
        duration_ms=fetched.duration_ms,
    )
