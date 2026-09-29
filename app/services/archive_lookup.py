"""Fallback source lookup via the Wayback Machine / archive.org (docs/TASKS_CITATION_
VERIFICATION.md T10, design decision 19).

Only ever consulted as a fallback from app/services/citation_verification.py: a live capture that
came back 404/410, or a live page that was reachable but simply didn't contain the cited quote.
Never a substitute for the live fetch citation_verification.py already attempts first (T4/T8) —
archive.org is slow and itself gets rate-limited for stretches of time (finding 8 in the TASKS
doc: 429/504/"Temporarily Offline" observed within one afternoon), so this module draws a hard
line between two different outcomes that must never be confused with each other (the critical
rule behind this whole task):

  - "archive.org was reached and genuinely has no snapshot of this URL" — a confirmed negative,
    returned as `None`. The caller keeps whatever verdict the live check already produced.
  - "archive.org could not be asked right now" (429, 5xx, timeout, connection error) — raised as
    `ArchiveUnavailable` and never caught here. The caller (app/services/verification_queue.py's
    `process_verification_job`) already defers and retries a whole job on an unexpected
    exception; letting this propagate reuses that machinery rather than inventing a second one,
    and guarantees a rate-limited archive.org can never get silently recorded as "the quote
    doesn't exist".

Rate limiting (1 request / 3s, design decision 19) is a single shared budget across both the CDX
lookup and the snapshot download, unlike app/services/source_capture.py's per-*target*-domain
throttle — there is only ever one target domain here (archive.org itself), never many.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import urlencode

import httpx

from app.services.source_extract import extract_html, extract_pdf

logger = logging.getLogger(__name__)

VERIFIER_VERSION = "1.0"

CDX_URL = "https://web.archive.org/cdx/search/cdx"
REQUEST_TIMEOUT_SECONDS = 60.0  # design decision 19
MIN_REQUEST_INTERVAL_SECONDS = 3.0  # design decision 19

# Module-level, process-lifetime state (production is a long-lived worker) — mirrors
# source_capture.py's own `_last_domain_request`, just keyed by nothing since there is only one
# target domain here. Tests reset this between runs (tests/test_archive_lookup.py).
_last_request_at: float | None = None


class ArchiveUnavailable(Exception):
    """archive.org itself could not be reached, or is rate-limiting/erroring (429/5xx/timeout/

    connection error) — never raised for "no snapshot exists" (see module docstring; that is a
    confirmed negative, returned as None instead). The caller must let this propagate so the
    verification job gets deferred and retried, never recorded as a permanent verdict.
    """


@dataclass(frozen=True)
class ArchiveSnapshot:
    """One Wayback CDX result: the timestamp `closest` picked, plus both URLs a caller needs —

    `fetch_url` (the machine-readable `id_` form, raw bytes with no Wayback toolbar/rewriting,
    for `fetch_snapshot_content` below) and `view_url` (the ordinary human-viewable Wayback page,
    for a UI "open this passage" link — app/services/verification_display.py special-cases
    `SourceDocument.method == "archive"` to use this instead of the live `requested_url`, which
    for an archive-backed verdict is exactly the page that's now gone or changed).
    """

    archive_timestamp: str
    fetch_url: str
    view_url: str


@dataclass(frozen=True)
class ArchivedContent:
    text: str
    page_starts: list[int] | None
    locations: list[dict] | None


def _throttle(*, sleep: Callable[[float], None]) -> None:
    global _last_request_at
    now = time.monotonic()
    if _last_request_at is not None:
        remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_at)
        if remaining > 0:
            sleep(remaining)
    _last_request_at = time.monotonic()


def _request(client: httpx.Client, url: str, *, sleep: Callable[[float], None]) -> httpx.Response:
    """One rate-limited GET against archive.org — the single chokepoint every archive.org call

    in this module goes through, so the 3s pacing and the ArchiveUnavailable classification only
    ever need to be right in one place.
    """
    _throttle(sleep=sleep)
    try:
        response = client.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        raise ArchiveUnavailable(f"timeout requesting {url}") from exc
    except httpx.RequestError as exc:
        raise ArchiveUnavailable(f"connection error requesting {url}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise ArchiveUnavailable(f"archive.org returned {response.status_code} for {url}")
    return response


def find_closest_snapshot(
    client: httpx.Client, url: str, *, target_date: datetime, sleep: Callable[[float], None] = time.sleep
) -> ArchiveSnapshot | None:
    """The Wayback snapshot of `url` closest to `target_date` with a 200 status (design decision

    19: `sort=closest`, `filter=statuscode:200`), or None when archive.org has genuinely never
    captured a working copy of this URL — a confirmed negative, distinct from ArchiveUnavailable
    (see module docstring).
    """
    params = {
        "url": url,
        "output": "json",
        "filter": "statuscode:200",
        "sort": "closest",
        "closest": target_date.strftime("%Y%m%d%H%M%S"),
        "limit": "1",
    }
    response = _request(client, f"{CDX_URL}?{urlencode(params)}", sleep=sleep)
    rows = response.json()
    if len(rows) < 2:  # rows[0] is the CDX header row; no header at all means no match either
        return None
    timestamp, original = rows[1][1], rows[1][2]
    return ArchiveSnapshot(
        archive_timestamp=timestamp,
        fetch_url=f"https://web.archive.org/web/{timestamp}id_/{original}",
        view_url=f"https://web.archive.org/web/{timestamp}/{original}",
    )


def fetch_snapshot_content(
    client: httpx.Client, snapshot: ArchiveSnapshot, *, sleep: Callable[[float], None] = time.sleep
) -> ArchivedContent | None:
    """Download and extract `snapshot`'s raw content — the same extract_html/extract_pdf

    app/services/source_capture.py uses for a live fetch, so an archived page gets the same
    location/PDF-page metadata a live one would. None when the snapshot's content had nothing
    checkable (e.g. a PDF with no extractable text) — archive.org answered fine, the snapshot
    itself just has nothing usable, so this is NOT an ArchiveUnavailable.
    """
    response = _request(client, snapshot.fetch_url, sleep=sleep)
    content_type = response.headers.get("content-type", "")
    is_pdf = "pdf" in content_type.lower() or snapshot.fetch_url.lower().split("?")[0].endswith(".pdf")

    if is_pdf:
        extracted_pdf = extract_pdf(response.content)
        if extracted_pdf is None:
            return None
        return ArchivedContent(text=extracted_pdf.text, page_starts=extracted_pdf.page_starts, locations=None)

    extracted_html = extract_html(response.text)
    if not extracted_html.text.strip():
        return None
    return ArchivedContent(text=extracted_html.text, page_starts=None, locations=extracted_html.locations)
