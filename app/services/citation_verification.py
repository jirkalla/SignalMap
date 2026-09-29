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
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.run import Citation, RawResponse
from app.models.verification import CitationVerification, SourceDocument, SourceText
from app.services.claims import derive_claim
from app.services.quote_match import ChunkMatch, match_quote

VERIFIER_VERSION = "1.0"

_QUOTE_PROVIDERS = ("anthropic", "perplexity")


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


def _latest_source_document(db: Session, url: str) -> SourceDocument | None:
    return db.scalar(
        select(SourceDocument).where(SourceDocument.requested_url == url).order_by(SourceDocument.fetched_at.desc()).limit(1)
    )


def _locate(match_start: int | None, locations: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The `{headings, collapsed, collapsed_title}` record (app/services/source_extract.py's

    shape) whose span contains `match_start`, or None when there's nothing to locate or no
    location data was captured for this document (e.g. a PDF, which has `page_starts` instead).
    """
    if match_start is None or not locations:
        return None
    for location in locations:
        if location["start"] <= match_start < location["end"]:
            return {
                "headings": location["headings"],
                "collapsed": location["collapsed"],
                "collapsed_title": location["collapsed_title"],
            }
    return None


def _locate_page(match_start: int | None, page_starts: list[int] | None) -> int | None:
    """The 1-indexed PDF page `match_start` falls on, from `SourceDocument.page_starts`

    (app/services/source_extract.py's `ExtractedPdf.page_starts`), or None for an HTML document
    (which has `locations` instead) or when there's nothing to locate.
    """
    if match_start is None or not page_starts:
        return None
    page_index = 0
    for i, start in enumerate(page_starts):
        if start <= match_start:
            page_index = i
        else:
            break
    return page_index + 1


def _fragment_dict(fragment: ChunkMatch) -> dict[str, Any]:
    return {
        "chunk": fragment.chunk,
        "kind": fragment.kind,
        "similarity": fragment.similarity,
        "matched_text": fragment.matched_text,
        "match_start": fragment.match_start,
        "match_end": fragment.match_end,
    }


def verify_citations_by_quote(db: Session, raw_response: RawResponse, *, now: datetime) -> None:
    """Run the literal-quote check for every citation on `raw_response` that has one to run

    (design decisions 1-3): Anthropic/Perplexity citations whose source has already been
    captured. Writes one `CitationVerification` row per checked citation — never touches an
    existing row (NFR-6, append-only).

    Silently does nothing for a citation when: its provider isn't Anthropic/Perplexity, it has
    no cited text to check (Perplexity citations with no matching search_results snippet), or
    its source hasn't been captured yet (`_latest_source_document` finds nothing — the capture
    job that just ran is exactly what makes this normally not the case, but a citation whose URL
    capture_url() itself skipped, e.g. `robots`, still leaves a `SourceDocument` row with an
    `error_reason` and no text, which IS handled, as `unverifiable`).
    """
    provider_code = raw_response.run.model.provider.code
    if provider_code not in _QUOTE_PROVIDERS:
        return

    citations = db.scalars(select(Citation).where(Citation.raw_response_id == raw_response.id)).all()
    for citation in citations:
        if not citation.source_url:
            continue
        quote_text = _cited_text(citation, provider_code, raw_response.raw_payload)
        if not quote_text:
            continue

        document = _latest_source_document(db, citation.source_url)
        if document is None:
            continue

        derived_claim = derive_claim(provider_code, citation, raw_response.rendered_text, raw_response.raw_payload)

        if document.error_reason is not None:
            db.add(
                CitationVerification(
                    citation_id=citation.id,
                    source_document_id=document.id,
                    claim_text=derived_claim.text if derived_claim else None,
                    claim_method=derived_claim.method if derived_claim else None,
                    check_type="quote",
                    verdict="unverifiable",
                    reason=document.error_reason,
                    verifier_version=VERIFIER_VERSION,
                )
            )
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
            # extracted-text case).
            db.add(
                CitationVerification(
                    citation_id=citation.id,
                    source_document_id=document.id,
                    claim_text=derived_claim.text if derived_claim else None,
                    claim_method=derived_claim.method if derived_claim else None,
                    check_type="quote",
                    verdict="unverifiable",
                    reason="no_checkable_text",
                    verifier_version=VERIFIER_VERSION,
                )
            )
            continue

        db.add(
            CitationVerification(
                citation_id=citation.id,
                source_document_id=document.id,
                claim_text=derived_claim.text if derived_claim else None,
                claim_method=derived_claim.method if derived_claim else None,
                check_type="quote",
                verdict=result.verdict,
                similarity=round(result.similarity, 3) if result.similarity is not None else None,
                matched_text=result.matched_text,
                match_start=result.match_start,
                match_end=result.match_end,
                page_number=_locate_page(result.match_start, document.page_starts),
                location=_locate(result.match_start, document.locations),
                fragments=[_fragment_dict(f) for f in result.fragments],
                verifier_version=VERIFIER_VERSION,
            )
        )
    db.commit()
