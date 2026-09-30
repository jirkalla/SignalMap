"""Tests for app/services/citation_verification.py (docs/TASKS_CITATION_VERIFICATION.md T8).

Runs/RawResponses/Citations/SourceDocuments/SourceTexts built directly against db_session (same
shape as tests/test_verification_queue.py) — no real network, no real capture; the "already
captured" state this module reads is constructed directly.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIModel, Prompt, Provider, RawResponse, Run
from app.models.run import Citation
from app.models.verification import CitationVerification, SourceDocument, SourceText
from app.services.citation_verification import verify_citations_by_quote

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _perplexity_model(db_session: Session) -> AIModel:
    """`seed` (tests/conftest.py) only provisions google_gemini/anthropic/openai — Perplexity is

    built here, once per test that needs it, the same way tests/test_backfill_sources.py builds
    a second Client rather than extending the shared fixture for a one-off need.
    """
    provider = Provider(code="perplexity", name="Perplexity")
    db_session.add(provider)
    db_session.flush()
    model = AIModel(provider_id=provider.id, model_name="perplexity-test-model", capability_tier="standard", supports_web_search=True, is_active=True)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _make_run(db_session: Session, seed: dict, prompt: Prompt, model: AIModel) -> Run:
    run = Run(
        prompt_id=prompt.id, model_id=model.id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="success",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_raw_response(db_session: Session, run: Run, *, rendered_text: str, raw_payload: dict | None = None) -> RawResponse:
    raw = RawResponse(
        run_id=run.id, raw_payload=raw_payload or {"answer": rendered_text}, rendered_text=rendered_text, has_citations=True
    )
    db_session.add(raw)
    db_session.commit()
    db_session.refresh(raw)
    return raw


def _add_citation(db_session: Session, raw: RawResponse, *, position: int, source_url: str, source_passage: str | None = None) -> Citation:
    citation = Citation(
        raw_response_id=raw.id, source_url=source_url, source_domain="example.com",
        citation_position=position, source_passage=source_passage,
    )
    db_session.add(citation)
    db_session.commit()
    db_session.refresh(citation)
    return citation


def _add_source_document(
    db_session: Session,
    *,
    url: str,
    text: str | None = None,
    error_reason: str | None = None,
    locations: list[dict] | None = None,
    page_starts: list[int] | None = None,
) -> SourceDocument:
    text_sha256 = None
    if text is not None:
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        db_session.add(SourceText(sha256=text_sha256, text=text, chars=len(text)))

    document = SourceDocument(
        requested_url=url, method="live", http_status=200 if text is not None else None,
        error_reason=error_reason, text_sha256=text_sha256, locations=locations, page_starts=page_starts,
        fetched_at=NOW, verifier_version="1.0",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _citation_verification(db_session: Session, citation_id: int) -> CitationVerification | None:
    return db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation_id))


# --- Anthropic (source_passage) -----------------------------------------------------------------


def test_writes_verified_exact_for_a_matching_anthropic_citation(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    # Shaped so derive_claim (T1) can actually resolve a claim: one text block, one citation
    # attached to it, url matching the Citation row below — anything less and derive_claim logs
    # a position-mismatch warning and returns None (by design, it never guesses).
    raw_payload = {
        "content": [
            {
                "type": "text",
                "text": "Acme is reliable.",
                "citations": [{"url": "https://example.com/a", "title": "A", "cited_text": "..."}],
            }
        ]
    }
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.", raw_payload=raw_payload)
    citation = _add_citation(
        db_session, raw, position=0, source_url="https://example.com/a",
        source_passage="Acme has been rated highly for reliability since 2019.",
    )
    _add_source_document(
        db_session, url="https://example.com/a",
        text="On our review page: Acme has been rated highly for reliability since 2019. Read more.",
    )

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification is not None
    assert verification.verdict == "verified_exact"
    assert verification.check_type == "quote"
    assert verification.similarity == 1
    assert verification.claim_text == "Acme is reliable."
    assert verification.claim_method == "anthropic_block"


def test_writes_unverifiable_with_the_capture_error_reason(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.")
    citation = _add_citation(
        db_session, raw, position=0, source_url="https://blocked.example.com/a", source_passage="Some claim text here.",
    )
    _add_source_document(db_session, url="https://blocked.example.com/a", error_reason="bot_challenge")

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification.verdict == "unverifiable"
    assert verification.reason == "bot_challenge"
    assert verification.matched_text is None


def test_skips_providers_with_nothing_to_quote_check(db_session: Session, seed, sample_prompt: Prompt):
    """google_gemini (seed's default model) gets the LLM paraphrase check instead (T11/T12) —

    the quote checker must write nothing at all for it.
    """
    run = _make_run(db_session, seed, sample_prompt, seed["model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.")
    _add_citation(db_session, raw, position=0, source_url="https://example.com/a", source_passage="irrelevant")
    _add_source_document(db_session, url="https://example.com/a", text="Acme is reliable, sources say.")

    verify_citations_by_quote(db_session, raw, now=NOW)

    assert db_session.scalars(select(CitationVerification)).all() == []


def test_skips_a_citation_whose_source_has_not_been_captured_yet(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.")
    _add_citation(db_session, raw, position=0, source_url="https://never-captured.example.com/a", source_passage="Some claim.")

    verify_citations_by_quote(db_session, raw, now=NOW)

    assert db_session.scalars(select(CitationVerification)).all() == []


def test_two_citations_to_the_same_url_each_get_their_own_row(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable and well established.")
    first = _add_citation(db_session, raw, position=0, source_url="https://example.com/a", source_passage="Acme is reliable.")
    second = _add_citation(db_session, raw, position=1, source_url="https://example.com/a", source_passage="Acme is well established.")
    _add_source_document(db_session, url="https://example.com/a", text="Acme is reliable and well established, per our records.")

    verify_citations_by_quote(db_session, raw, now=NOW)

    assert _citation_verification(db_session, first.id) is not None
    assert _citation_verification(db_session, second.id) is not None
    assert _citation_verification(db_session, first.id).id != _citation_verification(db_session, second.id).id


# --- Perplexity (snippet matched by URL) ---------------------------------------------------


def test_matches_perplexity_snippet_to_citation_by_url(db_session: Session, seed, sample_prompt: Prompt):
    model = _perplexity_model(db_session)
    run = _make_run(db_session, seed, sample_prompt, model)
    raw_payload = {
        "output": [
            {
                "type": "search_results",
                "results": [
                    {"url": "https://example.com/a", "snippet": "Acme has been rated highly for reliability since 2019."},
                    {"url": "https://example.com/b", "snippet": "Unrelated snippet for a different citation entirely here."},
                ],
            }
        ]
    }
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.", raw_payload=raw_payload)
    citation = _add_citation(db_session, raw, position=0, source_url="https://example.com/a")
    _add_source_document(
        db_session, url="https://example.com/a",
        text="On our review page: Acme has been rated highly for reliability since 2019. Read more.",
    )

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification is not None
    assert verification.verdict == "verified_exact"


def test_perplexity_citation_with_no_matching_snippet_is_skipped(db_session: Session, seed, sample_prompt: Prompt):
    model = _perplexity_model(db_session)
    run = _make_run(db_session, seed, sample_prompt, model)
    raw_payload = {"output": [{"type": "search_results", "results": []}]}
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.", raw_payload=raw_payload)
    _add_citation(db_session, raw, position=0, source_url="https://example.com/a")
    _add_source_document(db_session, url="https://example.com/a", text="Acme is reliable, per our records.")

    verify_citations_by_quote(db_session, raw, now=NOW)

    assert db_session.scalars(select(CitationVerification)).all() == []


def test_perplexity_snippet_with_only_short_table_cells_is_no_checkable_text(db_session: Session, seed, sample_prompt: Prompt):
    """Real case: run 290, adac.de/enyaq/1generation-facelift — a Perplexity snippet that's

    nothing but short Markdown-table cell values has no chunk over the 20-char floor. Recorded
    explicitly (unverifiable/no_checkable_text), not silently skipped.
    """
    model = _perplexity_model(db_session)
    run = _make_run(db_session, seed, sample_prompt, model)
    raw_payload = {
        "output": [
            {
                "type": "search_results",
                "results": [{"url": "https://example.com/a", "snippet": "2,4\n- **Sicherheit**\n1,3\n...\n1,3"}],
            }
        ]
    }
    raw = _make_raw_response(db_session, run, rendered_text="Acme scores 2.4.", raw_payload=raw_payload)
    citation = _add_citation(db_session, raw, position=0, source_url="https://example.com/a")
    _add_source_document(db_session, url="https://example.com/a", text="A page with lots of unrelated content on it entirely.")

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification is not None
    assert verification.verdict == "unverifiable"
    assert verification.reason == "no_checkable_text"


# --- location / page lookup -----------------------------------------------------------------


def test_html_match_carries_its_location_from_the_captured_document(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.")
    citation = _add_citation(
        db_session, raw, position=0, source_url="https://example.com/a", source_passage="Acme has been rated highly for reliability.",
    )
    text = "Intro text here. Acme has been rated highly for reliability. Closing text here."
    locations = [
        {"start": 0, "end": 17, "headings": ["Intro"], "collapsed": False, "collapsed_title": None},
        {"start": 17, "end": 62, "headings": ["Reviews", "Reliability"], "collapsed": True, "collapsed_title": "Details"},
        {"start": 62, "end": len(text), "headings": ["Closing"], "collapsed": False, "collapsed_title": None},
    ]
    _add_source_document(db_session, url="https://example.com/a", text=text, locations=locations)

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification.location == {"headings": ["Reviews", "Reliability"], "collapsed": True, "collapsed_title": "Details"}


def test_pdf_match_carries_its_page_number_from_page_starts(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt, seed["anthropic_model"])
    raw = _make_raw_response(db_session, run, rendered_text="Acme is reliable.")
    citation = _add_citation(
        db_session, raw, position=0, source_url="https://example.com/report.pdf", source_passage="Acme has been rated highly for reliability.",
    )
    page1 = "Cover page with nothing relevant on it at all here whatsoever. "
    page2 = "Acme has been rated highly for reliability. More detail follows on this page."
    text = page1 + page2
    _add_source_document(db_session, url="https://example.com/report.pdf", text=text, page_starts=[0, len(page1)])

    verify_citations_by_quote(db_session, raw, now=NOW)

    verification = _citation_verification(db_session, citation.id)
    assert verification.page_number == 2
