"""Tests for app/services/claim_judge.py and app/services/passages.py (docs/TASKS_CITATION_

VERIFICATION.md T12). No real API calls — every judge() call goes through FakeAdapter (registered
for 'anthropic' by tests/conftest.py's `_fake_adapter_registered`), never a real Anthropic/OpenAI/
Gemini request.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import JudgePayload
from app.models import AIModel, AIModelPriceComponent, Citation, Prompt, RawResponse, Run
from app.models.verification import CitationVerification, SourceDocument, SourceText
from app.services.claim_judge import judge_citations
from app.services.passages import select_passages
from tests.fake_adapter import FakeAdapter

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _make_run(db_session: Session, seed: dict, sample_prompt: Prompt) -> Run:
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id,
        persona_id=seed["persona"].id, trigger_type="manual", status="success",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_raw_response(db_session: Session, run: Run, *, rendered_text: str) -> RawResponse:
    raw = RawResponse(run_id=run.id, raw_payload={"answer": rendered_text}, rendered_text=rendered_text, has_citations=True)
    db_session.add(raw)
    db_session.commit()
    db_session.refresh(raw)
    return raw


def _add_citation(db_session: Session, raw: RawResponse, *, source_url: str, span: str, start: int, end: int) -> Citation:
    citation = Citation(
        raw_response_id=raw.id, source_url=source_url, source_domain="example.com", citation_position=0,
        cited_answer_span=span, answer_span_start=start, answer_span_end=end,
    )
    db_session.add(citation)
    db_session.commit()
    db_session.refresh(citation)
    return citation


def _add_source_document(db_session: Session, *, url: str, text: str | None = None, error_reason: str | None = None) -> SourceDocument:
    import hashlib

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


def _judge_model(db_session: Session, seed: dict) -> AIModel:
    """The judge model is a plain `ai_models` row the caller supplies (design decision 20) —

    reuses `seed["anthropic_model"]` since FakeAdapter is registered for 'anthropic'
    (tests/conftest.py), plus a price component so `cost_usd` comes out non-None.
    """
    model = seed["anthropic_model"]
    db_session.add(AIModelPriceComponent(ai_model_id=model.id, component_type="input", price_per_unit_usd=Decimal("1.000000"), effective_from=NOW))
    db_session.add(AIModelPriceComponent(ai_model_id=model.id, component_type="output", price_per_unit_usd=Decimal("5.000000"), effective_from=NOW))
    db_session.commit()
    return model


_CLAIM_SPAN = "Acme has been the top-rated provider in Germany for three years running."
_RENDERED_TEXT = _CLAIM_SPAN
_SOURCE_TEXT = (
    "Company background. Acme has been the top-rated provider in Germany for three years running. "
    "It was founded in 1998 and now employs over two thousand people across twelve countries."
)


def _valid_reply(verdict: str = "supported", quote: str | None = None) -> str:
    quote = quote if quote is not None else "Acme has been the top-rated provider in Germany for three years running."
    return f'{{"verdict": "{verdict}", "reason": "The page states this directly.", "quote": "{quote}"}}'


def test_judge_citations_writes_llm_supported_for_a_valid_reply(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, text=_SOURCE_TEXT)
    judge_model = _judge_model(db_session, seed)

    FakeAdapter.judge_payload_to_return = JudgePayload(text=_valid_reply(), token_usage={"input_tokens": 200, "output_tokens": 20})

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification is not None
    assert verification.check_type == "llm"
    assert verification.verdict == "llm_supported"
    assert verification.llm_reason == "The page states this directly."
    assert verification.llm_quote_found is True
    assert verification.needs_review is False
    assert verification.matched_text == "Acme has been the top-rated provider in Germany for three years running."
    assert verification.llm_model_id == judge_model.id
    assert verification.tokens_in == 200
    assert verification.tokens_out == 20
    assert float(verification.cost_usd) == round(200 / 1e6 * 1.0 + 20 / 1e6 * 5.0, 6)


def test_judge_citations_parses_a_reply_with_an_unescaped_quote(db_session: Session, seed, sample_prompt: Prompt):
    """docs/TASKS_CITATION_VERIFICATION.md finding 7 — a model occasionally leaves a literal `"`

    unescaped inside a string value, which breaks strict `json.loads`. The regex fallback must
    still recover the verdict/reason/quote.
    """
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, text=_SOURCE_TEXT)
    judge_model = _judge_model(db_session, seed)

    broken_json = (
        '{"verdict": "supported", "reason": "The page literally says "top-rated provider" here.", '
        '"quote": "Acme has been the top-rated provider in Germany for three years running."}'
    )
    FakeAdapter.judge_payload_to_return = JudgePayload(text=broken_json, token_usage={"input_tokens": 150, "output_tokens": 15})

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "llm_supported"
    assert verification.llm_reason == 'The page literally says "top-rated provider" here.'
    assert verification.llm_quote == "Acme has been the top-rated provider in Germany for three years running."
    assert verification.llm_quote_found is True


def test_judge_citations_flags_needs_review_when_quote_not_found_on_the_snapshot(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, text=_SOURCE_TEXT)
    judge_model = _judge_model(db_session, seed)

    FakeAdapter.judge_payload_to_return = JudgePayload(
        text=_valid_reply(quote="This exact sentence never appears anywhere on the captured page at all."),
        token_usage={"input_tokens": 100, "output_tokens": 10},
    )

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "llm_supported"  # the verdict is still recorded (design decision 23)
    assert verification.llm_quote_found is False
    assert verification.needs_review is True
    assert verification.matched_text is None


def test_judge_citations_writes_unverifiable_for_a_failed_capture(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, error_reason="http_403")
    judge_model = _judge_model(db_session, seed)

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "unverifiable"
    assert verification.reason == "http_403"
    assert verification.check_type == "llm"
    # No judge() call was made at all for an already-failed capture — nothing to assert on the
    # FakeAdapter beyond this test not crashing on FakeAdapter.judge_payload_to_return being unset.


def test_judge_citations_writes_unverifiable_for_a_rate_limited_capture(db_session: Session, seed, sample_prompt: Prompt):
    """Regression test for the 2026-09-29 incident: `error_reason="http_429"` used to be absent

    from `UNVERIFIABLE_REASONS` (migration 0041 added it), so copying it across in
    `judge_citations` hit `citation_verifications`' CHECK constraint on `commit()` — rolling back
    every judgement for the response, including already-paid LLM calls for its other citations.
    """
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, error_reason="http_429")
    judge_model = _judge_model(db_session, seed)

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "unverifiable"
    assert verification.reason == "http_429"
    assert verification.check_type == "llm"


def test_judge_citations_skips_when_source_not_captured_yet(db_session: Session, seed, sample_prompt: Prompt):
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    judge_model = _judge_model(db_session, seed)

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    assert db_session.scalars(select(CitationVerification)).all() == []


def test_judge_citations_flags_needs_review_for_an_unrecognized_verdict_and_still_records_cost(
    db_session: Session, seed, sample_prompt: Prompt
):
    run = _make_run(db_session, seed, sample_prompt)
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    citation = _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    _add_source_document(db_session, url=citation.source_url, text=_SOURCE_TEXT)
    judge_model = _judge_model(db_session, seed)

    FakeAdapter.judge_payload_to_return = JudgePayload(text="I refuse to answer in JSON today.", token_usage={"input_tokens": 80, "output_tokens": 8})

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification.verdict == "unverifiable"
    assert verification.needs_review is True
    assert verification.tokens_in == 80
    assert verification.tokens_out == 8
    assert verification.cost_usd is not None


def test_judge_citations_skips_non_llm_providers(db_session: Session, seed, sample_prompt: Prompt):
    """Anthropic already gets the free literal-quote check (T8) — judge_citations must do nothing

    for it, even if a citation superficially looks judgeable.
    """
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["anthropic_model"].id, market_id=seed["market"].id,
        persona_id=seed["persona"].id, trigger_type="manual", status="success",
    )
    db_session.add(run)
    db_session.flush()
    raw = _make_raw_response(db_session, run, rendered_text=_RENDERED_TEXT)
    _add_citation(db_session, raw, source_url="https://example.com/a", span=_CLAIM_SPAN, start=0, end=len(_CLAIM_SPAN))
    judge_model = _judge_model(db_session, seed)

    judge_citations(db_session, raw, judge_model=judge_model, now=NOW)

    assert db_session.scalars(select(CitationVerification)).all() == []


# --- select_passages (BM25, design decision 21) -------------------------------------------------


def test_select_passages_ranks_the_relevant_sentence_first():
    passages = select_passages(_CLAIM_SPAN, _SOURCE_TEXT)

    assert passages
    assert "top-rated provider" in passages[0]


def test_select_passages_caps_at_k_and_max_chars():
    long_source = " ".join(f"Sentence number {i} talks about something else entirely." for i in range(50))
    long_source += " Acme has been the top-rated provider in Germany for three years running."

    passages = select_passages(_CLAIM_SPAN, long_source, k=3, max_chars=200)

    assert len(passages) <= 3
    assert sum(len(p) for p in passages) <= 200


def test_select_passages_returns_empty_for_completely_unrelated_text():
    assert select_passages(_CLAIM_SPAN, "Der Himmel ist blau und die Sonne scheint heute sehr stark.") == []


def test_select_passages_returns_empty_for_text_with_no_sentences():
    assert select_passages(_CLAIM_SPAN, "   ") == []
