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

from app.models.verification import UNVERIFIABLE_REASONS, SourceDocument, SourceText
from app.services.rate_limit import throttle
from app.services.source_extract import extract_html, extract_pdf, sanitize_extracted_text

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

# design decision 9's "1s between requests to the same domain" — the "at most 1 request per
# domain" half of that decision only holds WITHIN one process (verification_jobs are claimed and
# processed one at a time per process, design decision 3, T5, so at most one capture_url call is
# ever in flight from a single process for any domain). Production runs 4 such processes at once
# (`docker compose --scale worker=4`), so the minimum spacing itself is enforced across processes
# too, via `app/services/rate_limit.py`'s DB-backed `throttle()` — a plain in-process dict here
# used to let 2+ workers each pace independently against the same domain, multiplying the real
# aggregate rate by however many of them collided (code-review finding, 2026-09-30).
MIN_DOMAIN_INTERVAL_SECONDS = 1.0

_robots_cache: dict[str, RobotFileParser] = {}

# Short body (design decision 8: "< 1500 characters") matching a known bot-protection interstitial,
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
    # follow_redirects=False: `_resolve_and_fetch` walks redirects itself, hop by hop, so that
    # robots.txt is checked for every host the chain passes through (design decision 1).
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    )


def _throttle_domain(db: Session, domain: str, *, sleep: Callable[[float], None]) -> None:
    """Block (via `sleep`) until at least `MIN_DOMAIN_INTERVAL_SECONDS` have passed since the

    last request to `domain` from ANY worker process sharing this database (code-review finding,
    2026-09-30 — see `MIN_DOMAIN_INTERVAL_SECONDS`'s own comment). `sleep` is injected (defaults
    to `time.sleep` in `capture_url`) so tests can pass a no-op and never actually wait.
    """
    throttle(db, domain, interval_seconds=MIN_DOMAIN_INTERVAL_SECONDS, sleep=sleep)


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
            # follow_redirects=True per request: the client itself never follows redirects, but
            # a robots.txt that 301s (http -> https, bare -> www) must still be read — otherwise
            # the empty 3xx body would parse as "allow everything" (RFC 9309 §2.3.1.2).
            response = client.get(urljoin(origin, "/robots.txt"), follow_redirects=True)
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


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Known redirect gateways: (host, path prefix). robots.txt is deliberately NOT applied to these,
# and only for ONE hop with no body read (design decision 2). Gemini grounding citations are
# `vertexaisearch.cloud.google.com/grounding-api-redirect/...` links whose robots.txt says
# `Disallow: /grounding-api-redirect` — that stops the gateway from being INDEXED, not a person
# from clicking the one link a provider handed them as a citation. Fetching only its `Location`
# header is exactly what the user's own browser would do, and robots.txt of the page the link
# leads to is still enforced in full. This is a URL pattern, not a provider list.
_REDIRECT_GATEWAYS: tuple[tuple[str, str], ...] = (
    ("vertexaisearch.cloud.google.com", "/grounding-api-redirect/"),
)


def _is_redirect_gateway(url: str) -> bool:
    parts = urlsplit(url)
    return any(
        parts.hostname == host and parts.path.startswith(prefix) for host, prefix in _REDIRECT_GATEWAYS
    )


class _RobotsBlocked(Exception):
    """Raised internally when robots.txt disallows a hop — caught in `capture_url`."""


@dataclass
class _Trace:
    """The last URL a capture attempt reached or tried, updated as `_resolve_and_fetch` walks the
    chain — so a failure part-way (robots, timeout, too large) can still record where it ended.
    """

    last_url: str


def _redirect_target(current: str, status: int, headers: httpx.Headers) -> str | None:
    """The absolute next URL when this response is a redirect, else None."""
    location = headers.get("location")
    if status not in _REDIRECT_STATUSES or not location:
        return None
    target = urljoin(current, location)
    if urlsplit(target).scheme not in ("http", "https"):
        raise httpx.UnsupportedProtocol(
            f"redirect to unsupported scheme: {target!r}", request=httpx.Request("GET", current)
        )
    return target


def _peek(client: httpx.Client, url: str) -> _FetchResult:
    """One GET whose body is never read — enough for a gateway's status and `Location` header."""
    started = time.perf_counter()
    with client.stream("GET", url, follow_redirects=False) as response:
        return _FetchResult(
            final_url=url,
            status=response.status_code,
            headers=response.headers,
            body=b"",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _resolve_and_fetch(
    db: Session,
    client: httpx.Client,
    url: str,
    *,
    trace: _Trace,
    sleep: Callable[[float], None],
) -> _FetchResult:
    """Follow redirects by hand, hop by hop (design decisions 1-3).

    Every hop except a known gateway (`_REDIRECT_GATEWAYS`) is checked against robots.txt of ITS
    host and paced per that host — so a Gemini citation is throttled as its target domain, not as
    the shared `vertexaisearch` gateway. `trace.last_url` tracks the hop in progress. Raises
    `_RobotsBlocked`, `_TooLarge`, or an `httpx.RequestError` (incl. `TooManyRedirects`).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        trace.last_url = current
        if _is_redirect_gateway(current):
            result = _peek(client, current)
        else:
            if not _robots_allowed(client, current):
                raise _RobotsBlocked()
            _throttle_domain(db, urlsplit(current).netloc, sleep=sleep)
            result = _fetch(client, current)
        target = _redirect_target(current, result.status, result.headers)
        if target is None:
            return result
        current = target
    trace.last_url = current
    raise httpx.TooManyRedirects(
        f"more than {MAX_REDIRECTS} redirects", request=httpx.Request("GET", url)
    )


def _fetch(client: httpx.Client, url: str) -> _FetchResult:
    started = time.perf_counter()
    with client.stream("GET", url, follow_redirects=False) as response:
        if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
            # A redirect's own body is irrelevant — only the next hop matters.
            return _FetchResult(
                final_url=str(response.url),
                status=response.status_code,
                headers=response.headers,
                body=b"",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
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
    if error_reason is not None and error_reason not in UNVERIFIABLE_REASONS:
        # Defense in depth (code-review finding, 2026-09-30 round 2): `source_documents.error_
        # reason` is itself free-form/not CHECK-constrained (design decision 28), but every value
        # this module produces is EXPECTED to eventually be copyable into `citation_verifications.
        # reason`, which IS constrained to UNVERIFIABLE_REASONS — a value outside that set would
        # raise an IntegrityError deep in a worker job the moment `verify_citations_by_quote`/
        # `judge_citations` tried to copy it across (the exact bug class migrations 0038/0041/0042
        # each patched reactively for one specific value). Caught and normalized HERE, at the one
        # chokepoint every capture outcome passes through, instead of letting it surface later as
        # an opaque DB error with no indication which call site introduced the bad value.
        logger.error(
            "source_capture: %r is not in UNVERIFIABLE_REASONS — storing as 'http_other' instead (this is a bug, not an expected outcome)",
            error_reason,
        )
        error_reason = "http_other"

    text_sha256 = None
    if text is not None:
        # Safety net (docs/TASKS_CITATION_HARDENING.md T4): extraction already strips NUL, but a
        # NUL reaching this insert would raise a DataError and fail the whole job — and the hash
        # below must describe exactly the text that gets stored.
        text = sanitize_extracted_text(text)
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

    not (design decisions 6-13). Order of operations: 24h cache, then the redirect chain hop by
    hop (`_resolve_and_fetch`: per hop `robots.txt` then per-domain pacing then fetch — known
    gateways excepted), then challenge detection, then extraction.

    `now` drives both the cache check and the row's own `fetched_at` (never `datetime.now()`
    internally) — deterministic and test-injectable, same convention as
    `app/services/run_execution.py`/`app/worker.py`. `sleep` likewise — see
    `MIN_DOMAIN_INTERVAL_SECONDS`.
    """
    cached = _cached_document(db, url, now=now)
    if cached is not None:
        return cached

    trace = _Trace(last_url=url)

    def _failed(reason: str) -> SourceDocument:
        # `final_url` = the last hop reached/attempted, when that isn't the requested URL itself.
        return _store(
            db,
            requested_url=url,
            now=now,
            final_url=trace.last_url if trace.last_url != url else None,
            error_reason=reason,
        )

    try:
        fetched = _resolve_and_fetch(db, client, url, trace=trace, sleep=sleep)
    except _RobotsBlocked:
        return _failed("robots")
    except _TooLarge:
        return _failed("too_large")
    except httpx.TimeoutException:
        return _failed("timeout")
    except httpx.RequestError as exc:
        # No dedicated code for a generic connection failure (DNS, connection refused, too many
        # redirects, ...) in design decision 28's reason list — bucketed under "timeout" rather
        # than inventing a new one, since both mean the same thing to an analyst: this source
        # could not be reached this time.
        logger.info("capture_url: connection error for %s: %s", url, exc)
        return _failed("timeout")

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
        # Only the codes UNVERIFIABLE_REASONS actually enumerates (app/models/verification.py)
        # get their own value — any other 4xx (401, 402, 405, 406, 408, 451, ...) maps to the
        # generic "http_other" bucket instead of an unbounded f"http_{status}" string. That
        # unbounded fallback used to reach citation_verifications.reason's CHECK constraint
        # unvalidated, raising an IntegrityError on the first unhandled code (already hit twice
        # in production for 410 and 429, migrations 0038/0041) — "http_other" closes the gap for
        # every future code at once instead of adding one more reactive migration per status.
        if fetched.status in (404, 410, 429):
            reason = f"http_{fetched.status}"
        elif fetched.status >= 500:
            reason = "http_5xx"
        else:
            reason = "http_other"
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
