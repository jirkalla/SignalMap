"""Orchestrates the literal-quote check for one response's citations (docs/TASKS_CITATION_

VERIFICATION.md T8, design decisions 1-3, 18). Only two providers have anything a literal quote
check can run against (design decision 4/T1): Anthropic's `citation.source_passage`
(`cited_text`, already stored) and Perplexity's `snippet` (buried in `raw_payload`'s
`search_results`, matched to a citation by URL — Perplexity's citations carry no passage of
their own). Every other provider is skipped here entirely — not an oversight, there is nothing
to check them against (OpenAI/Gemini get the LLM paraphrase check instead, T11/T12; xAI is
"reachable or not", not a quote, and has no task wired up for it yet).

Called from `app/services/verification_queue.py`'s `process_verification_job`, right after a
capture job finishes fetching that response's citation URLs — "the literal check is free and
always runs" (design decision 1) is what makes it safe to run inline in the same job rather than
queuing a separate one.

`client`/`sleep` (both optional, T10) additionally let this module fall back to an archive.org
snapshot (app/services/archive_lookup.py) when the live capture came back 404/410, or the quote
wasn't found on an otherwise-reachable live page (design decision 19). `client=None` (the
default, and what every pre-T10 test still passes implicitly) skips the fallback entirely and
behaves exactly as before — archive.org is only ever consulted when a real client is supplied,
i.e. from `process_verification_job`.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.run import Citation, RawResponse
from app.models.verification import CitationVerification, SourceDocument, SourceText
from app.services import archive_lookup
from app.services.archive_lookup import ArchivedContent, ArchiveSnapshot
from app.services.claims import derive_claim
from app.services.quote_match import ChunkMatch, QuoteMatchResult, match_quote
from app.services.source_extract import sanitize_extracted_text
from app.services.verification_display import has_verification_since, latest_source_document as _latest_source_document, locate as _locate, locate_page as _locate_page

logger = logging.getLogger(__name__)

VERIFIER_VERSION = "1.0"

_QUOTE_PROVIDERS = ("anthropic", "perplexity")

# design decision 19 — a live capture that came back one of these is "the page is gone", the
# other archive.org trigger (besides "reachable but the quote just isn't there", handled inline
# in the main loop below). 'http_410' only became a legal citation_verifications.reason value in
# migration 0038 (found while wiring this up — see that migration's own docstring); source_capture
# .py (T4) has always been able to produce it on source_documents, which has no such constraint.
_PAGE_GONE_REASONS = ("http_404", "http_410")


@dataclass(frozen=True)
class QuoteCheckOutcome:
    """What one `verify_citations_by_quote` pass got through.

    `complete` — every citation was checked (False when the shared `deadline` cut the pass short).
    `archive_deferred` — how many citations were left WITHOUT a verdict because archive.org could
    not be asked right now (`ArchiveUnavailable`); the caller must retry the job for those
    (docs/TASKS_CITATION_HARDENING.md T3, design decision 6). Always 0 on a `final_attempt`, where
    they get an `unverifiable`/`archive_unavailable` verdict instead.
    """

    complete: bool
    archive_deferred: int = 0


def _perplexity_snippet(raw_payload: dict, url: str) -> str | None:
    """The `snippet` of the first `search_results` result matching `url` — mirrors

    `app/adapters/perplexity.py`'s own walk of `raw_payload["output"]`, since that shape is
    defined there, not duplicated as a second parser of the same JSON.
    """
    for item in raw_payload.get("output") or []:
        if (item or {}).get("type") != "search_results":
            continue
        for result in item.get("results") or []:
            if (result or {}).get("url") == url:
                return result.get("snippet")
    return None


def _cited_text(citation: Citation, provider_code: str, raw_payload: dict) -> str | None:
    """The provider-specific literal text to check `citation` against — None when this provider

    has nothing (design decision 4): Perplexity's OWN citations carry no passage, only the
    `search_results` item's snippet, looked up by URL.
    """
    if provider_code == "anthropic":
        return citation.source_passage
    if provider_code == "perplexity":
        return _perplexity_snippet(raw_payload, citation.source_url) if citation.source_url else None
    return None


def _fragment_dict(fragment: ChunkMatch) -> dict[str, Any]:
    return {
        "chunk": fragment.chunk,
        "kind": fragment.kind,
        "similarity": fragment.similarity,
        "matched_text": fragment.matched_text,
        "match_start": fragment.match_start,
        "match_end": fragment.match_end,
    }


def _store_archive_document(
    db: Session, *, requested_url: str, snapshot: ArchiveSnapshot, now: datetime, content: ArchivedContent
) -> SourceDocument:
    """Insert one `source_documents` row for an archive.org snapshot — always a new row (NFR-6).

    Mirrors app/services/source_capture.py's own `_store` (method='live') rather than sharing it:
    source_capture.py is T4's module, untouched by T10, and the two have diverged enough (no
    http_status/challenge_vendor/bytes/duration_ms worth recording for an archived fetch, but a
    method/archive_timestamp/final_url a live row never sets) that sharing one function would need
    almost as many parameters as writing this one, smaller version twice.

    `final_url` is the ordinary human-viewable Wayback page (`snapshot.view_url`, not the `id_`
    form this was actually fetched from) — app/services/verification_display.py's `_open_url`
    (T9) uses it as the "open this passage" link's base for a `method='archive'` document, since
    `requested_url` here is deliberately still the original (now gone/changed) live URL, kept for
    identity/lookup, not for linking to.
    """
    # Safety net (docs/TASKS_CITATION_HARDENING.md T4) — same reason as source_capture._store:
    # a NUL must never reach the insert, and the hash must describe the stored text.
    text = sanitize_extracted_text(content.text)
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db.execute(
        pg_insert(SourceText)
        .values(sha256=text_sha256, text=text, chars=len(text))
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    document = SourceDocument(
        requested_url=requested_url,
        final_url=snapshot.view_url,
        method="archive",
        archive_timestamp=snapshot.archive_timestamp,
        http_status=200,
        text_sha256=text_sha256,
        page_starts=content.page_starts,
        locations=content.locations,
        fetched_at=now,
        verifier_version=archive_lookup.VERIFIER_VERSION,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _try_archive_fallback(
    db: Session,
    client: httpx.Client,
    *,
    citation: Citation,
    quote_text: str,
    provider_code: str,
    target_date: datetime,
    now: datetime,
    sleep: Callable[[float], None],
) -> tuple[SourceDocument, QuoteMatchResult] | None:
    """Look up and check an archive.org snapshot of `citation.source_url` — returns

    `(archive_document, match_result)` only when the archive FULLY verifies the quote (design
    decision 19: `page_changed`/`archive_only` both mean the archive HAS the quote, not "partially
    has" — there is no archive-specific `partially_found` in the verdict enum). Returns None when
    archive.org has no snapshot at all, or its snapshot doesn't fully verify either — in both
    cases the caller keeps whatever verdict the live check already produced. Lets
    `archive_lookup.ArchiveUnavailable` propagate unchanged (see that module's docstring): a
    429/5xx/timeout from archive.org is never recorded as a verdict here — `verify_citations_by_
    quote` catches it per citation and decides (retry later, or a final `archive_unavailable`).
    """
    snapshot = archive_lookup.find_closest_snapshot(db, client, citation.source_url, target_date=target_date, sleep=sleep)
    if snapshot is None:
        return None
    content = archive_lookup.fetch_snapshot_content(db, client, snapshot, sleep=sleep)
    if content is None:
        return None
    result = match_quote(quote_text, provider_code, content.text)
    if result is None or result.verdict not in ("verified_exact", "verified_normalized"):
        return None
    archive_document = _store_archive_document(db, requested_url=citation.source_url, snapshot=snapshot, now=now, content=content)
    return archive_document, result


def verify_citations_by_quote(
    db: Session,
    raw_response: RawResponse,
    *,
    now: datetime,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    since: datetime | None = None,
    deadline: float | None = None,
    final_attempt: bool = False,
) -> QuoteCheckOutcome:
    """Run the literal-quote check for every citation on `raw_response` that has one to run

    (design decisions 1-3): Anthropic/Perplexity citations whose source has already been
    captured. Writes one `CitationVerification` row per checked citation — never touches an
    existing row (NFR-6, append-only). Commits after each citation, not once at the end (code-
    review finding, 2026-09-30): a single trailing commit meant one citation's failure discarded
    every other citation's already-computed verdict from the same call.

    `deadline` (a `time.monotonic()` value, typically the whole job's shared time budget from
    `process_verification_job`, code-review finding 2026-09-30 round 2) makes this bail out before
    a citation-heavy response's verify phase can run long enough to approach the job's lease
    expiry — without it, a long-running job could still be `leased` past `lease_expires_at` while
    genuinely still working, letting a second worker reclaim and concurrently double-process the
    same job. `QuoteCheckOutcome.complete` is True when every citation was checked, False when the
    deadline cut the pass short (caller must treat that as incomplete, not done — the per-citation
    `since` check above means a later pass resumes rather than restarts).

    `archive_lookup.ArchiveUnavailable` (archive.org rate-limiting/erroring) is handled PER CITATION
    (docs/TASKS_CITATION_HARDENING.md T3, design decision 6) — it used to escape this function and
    fail the whole job, so every citation after the unlucky one was lost once the job ran out of
    attempts. Now that citation is left without a verdict, counted in `archive_deferred`, and the
    loop carries on; the caller defers the job, and the retry's `since` check re-processes only
    those. On `final_attempt=True` (the job's last try) there is no later retry, so such a
    citation gets `unverifiable`/`archive_unavailable` against its LIVE source document instead —
    the live outcome (404, or a quote not found) is not repeated into the verdict, since the check
    that could have confirmed or overturned it never completed; it stays on the linked document.

    `since` (typically `job.created_at`, passed by `process_verification_job`) skips a citation
    that already has a `check_type='quote'` row created at/after `since` — this is what makes a
    retried attempt of the SAME job resume instead of re-verifying (and duplicating) citations an
    earlier attempt already committed, while a genuinely new job (created later) still re-checks
    everything, since old rows all have `created_at < since` for it.

    Silently does nothing for a citation when: its provider isn't Anthropic/Perplexity, it has
    no cited text to check (Perplexity citations with no matching search_results snippet), or
    its source hasn't been captured yet (`_latest_source_document` finds nothing — the capture
    job that just ran is exactly what makes this normally not the case, but a citation whose URL
    capture_url() itself skipped, e.g. `robots`, still leaves a `SourceDocument` row with an
    `error_reason` and no text, which IS handled, as `unverifiable`).

    When `client` is given (T10, design decision 19), a live 404/410 or a live page that didn't
    contain the quote gets one more attempt against an archive.org snapshot before settling for
    `unverifiable`/`not_found` — see `_try_archive_fallback`. `client=None` (the default) skips
    this entirely, unchanged from pre-T10 behavior.
    """
    provider_code = raw_response.run.model.provider.code
    if provider_code not in _QUOTE_PROVIDERS:
        return QuoteCheckOutcome(complete=True)

    target_date = raw_response.run.started_at
    # Captured once, up front, rather than read via `raw_response.raw_payload`/`.rendered_text`
    # inside the loop below (code-review finding, 2026-09-30 round 2): `SessionLocal` defaults to
    # `expire_on_commit=True`, so the per-citation `db.commit()` this function now does would
    # otherwise silently force a fresh SELECT of this response's full JSONB/Text columns on every
    # subsequent citation, for no reason — nothing about `raw_response` itself changes mid-loop.
    raw_payload = raw_response.raw_payload
    rendered_text = raw_response.rendered_text
    response_id = raw_response.id

    def _row(
        citation: Citation,
        derived_claim,
        *,
        source_document: SourceDocument,
        verdict: str,
        reason: str | None = None,
        result: QuoteMatchResult | None = None,
    ) -> CitationVerification:
        return CitationVerification(
            citation_id=citation.id,
            source_document_id=source_document.id,
            claim_text=derived_claim.text if derived_claim else None,
            claim_method=derived_claim.method if derived_claim else None,
            check_type="quote",
            verdict=verdict,
            reason=reason,
            similarity=round(result.similarity, 3) if result and result.similarity is not None else None,
            matched_text=result.matched_text if result else None,
            match_start=result.match_start if result else None,
            match_end=result.match_end if result else None,
            page_number=_locate_page(result.match_start, source_document.page_starts) if result else None,
            location=_locate(result.match_start, source_document.locations) if result else None,
            fragments=[_fragment_dict(f) for f in result.fragments] if result else None,
            verifier_version=VERIFIER_VERSION,
        )

    archive_deferred = 0

    def _archive_unavailable(citation: Citation, derived_claim, document: SourceDocument) -> None:
        """archive.org could not be asked for this citation: record it (last attempt) or leave it
        for the job's retry (every earlier attempt) — see this function's docstring.
        """
        nonlocal archive_deferred
        if final_attempt:
            db.add(_row(citation, derived_claim, source_document=document, verdict="unverifiable", reason="archive_unavailable"))
            db.commit()
        else:
            archive_deferred += 1
            logger.warning(
                "verify_citations_by_quote: archive.org unavailable for citation %s (%s) — leaving it for the job's retry",
                citation.id,
                citation.source_url,
            )

    citations = db.scalars(select(Citation).where(Citation.raw_response_id == response_id)).all()
    for citation in citations:
        if deadline is not None and time.monotonic() > deadline:
            return QuoteCheckOutcome(complete=False, archive_deferred=archive_deferred)
        if not citation.source_url:
            continue
        quote_text = _cited_text(citation, provider_code, raw_payload)
        if not quote_text:
            continue
        if since is not None and has_verification_since(db, citation.id, "quote", since):
            continue

        document = _latest_source_document(db, citation.source_url)
        if document is None:
            continue

        derived_claim = derive_claim(provider_code, citation, rendered_text, raw_payload)

        if document.error_reason is not None:
            archived = None
            if client is not None and document.error_reason in _PAGE_GONE_REASONS:
                try:
                    archived = _try_archive_fallback(
                        db, client, citation=citation, quote_text=quote_text, provider_code=provider_code,
                        target_date=target_date, now=now, sleep=sleep,
                    )
                except archive_lookup.ArchiveUnavailable:
                    _archive_unavailable(citation, derived_claim, document)
                    continue
            if archived is not None:
                archive_document, result = archived
                db.add(_row(citation, derived_claim, source_document=archive_document, verdict="archive_only", result=result))
            else:
                db.add(_row(citation, derived_claim, source_document=document, verdict="unverifiable", reason=document.error_reason))
            db.commit()
            continue

        source_text_row = db.get(SourceText, document.text_sha256)
        result = match_quote(quote_text, provider_code, source_text_row.text)
        if result is None:
            # Every chunk of the quote fell under quote_match's minimum length — e.g. a
            # Perplexity snippet that's nothing but short Markdown-table cell values (verified
            # against a real citation, run 290: "2,4\n- **Sicherheit**\n1,3..."). Recorded
            # explicitly, not silently skipped — otherwise "not yet checked" and "checked, had
            # nothing checkable" look identical from the outside. Reuses the existing
            # `no_checkable_text` reason (design decision 28) rather than inventing a new one;
            # it already means exactly this at the capture layer (source_capture.py's empty-
            # extracted-text case). Not an archive.org trigger (design decision 19: only 404/410
            # or "quote not found", not "quote too short to check meaningfully").
            db.add(_row(citation, derived_claim, source_document=document, verdict="unverifiable", reason="no_checkable_text"))
            db.commit()
            continue

        if result.verdict == "not_found" and client is not None:
            try:
                archived = _try_archive_fallback(
                    db, client, citation=citation, quote_text=quote_text, provider_code=provider_code,
                    target_date=target_date, now=now, sleep=sleep,
                )
            except archive_lookup.ArchiveUnavailable:
                _archive_unavailable(citation, derived_claim, document)
                continue
            if archived is not None:
                archive_document, archive_result = archived
                db.add(_row(citation, derived_claim, source_document=archive_document, verdict="page_changed", result=archive_result))
                db.commit()
                continue

        db.add(_row(citation, derived_claim, source_document=document, verdict=result.verdict, result=result))
        db.commit()
    return QuoteCheckOutcome(complete=True, archive_deferred=archive_deferred)
