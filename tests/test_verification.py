"""Tests for app/routers/verification.py (docs/TASKS_CITATION_VERIFICATION.md T14, design

decision 27) — blind labeling (`/verification/label`) and reviewing one specific LLM verdict
(the run detail page's "Souhlasím / Nesouhlasím"). No real API calls — every fixture inserts
`citation_verifications` rows directly, the same "build the row, call the route, assert on the
row" shape as tests/test_citation_verification.py.
"""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIModel, Citation, Prompt, RawResponse, Run, User
from app.models.verification import CitationVerification, SourceDocument, SourceText, VerificationLabel
from app.routers.verification import _next_blind_citation_id
from tests.conftest import TEST_USER_PASSWORD

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _make_llm_judged_citation(
    db_session: Session, seed: dict, sample_prompt: Prompt, *, model_key: str = "model", verdict: str = "llm_supported", position: int = 0
) -> tuple[Citation, CitationVerification]:
    """One run (provider from `seed[model_key]`) with one citation already carrying a

    `check_type='llm'` verdict AND a captured source — everything `/verification/label` and the
    review route need. `claim_text`/`cited_answer_span` line up (same shape tests/test_claim_judge.py
    uses) so `derive_claim` succeeds and passages can be selected.
    """
    claim_text = "Acme is known for reliability."
    model = seed[model_key]
    run = Run(
        prompt_id=sample_prompt.id, model_id=model.id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="success", started_at=NOW,
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={"answer": claim_text}, rendered_text=claim_text, has_citations=True)
    db_session.add(raw)
    db_session.flush()
    citation = Citation(
        raw_response_id=raw.id, source_url=f"https://example.com/{model_key}/{position}", source_title="Example", source_domain="example.com",
        citation_position=position, cited_answer_span=claim_text, answer_span_start=0, answer_span_end=len(claim_text),
    )
    db_session.add(citation)
    db_session.flush()

    # Varied per call (model_key/position) so SourceText.sha256 (its own primary key) never
    # collides when a test builds several citations, each with their own captured source.
    source_text = f"Company background for {model_key}#{position}. {claim_text} Founded long ago."
    text_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    db_session.add(SourceText(sha256=text_sha256, text=source_text, chars=len(source_text)))
    document = SourceDocument(requested_url=citation.source_url, method="live", http_status=200, text_sha256=text_sha256, fetched_at=NOW, verifier_version="1.0")
    db_session.add(document)
    db_session.flush()

    verification = CitationVerification(
        citation_id=citation.id, source_document_id=document.id, claim_text=claim_text, check_type="llm",
        verdict=verdict, llm_reason="The page states this.", llm_quote=claim_text, llm_quote_found=True,
        verifier_version="1.0", created_at=NOW,
    )
    db_session.add(verification)
    db_session.commit()
    db_session.refresh(citation)
    db_session.refresh(verification)
    return citation, verification


# --- stratified sampling ------------------------------------------------------------------------


def test_next_blind_citation_prefers_the_least_labeled_stratum(db_session: Session, seed, sample_prompt: Prompt, editor_user: User, admin_user: User):
    supported_a, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, model_key="model", verdict="llm_supported", position=0)
    supported_b, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, model_key="model", verdict="llm_supported", position=1)
    partial, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, model_key="model", verdict="llm_partial", position=2)

    # The 'llm_supported' stratum already has one label (by a DIFFERENT user) — the 'llm_partial'
    # stratum has none, so it must be offered next even though editor_user has labeled neither.
    db_session.add(VerificationLabel(citation_id=supported_a.id, user_id=admin_user.id, verdict="supported", mode="blind"))
    db_session.commit()

    next_id = _next_blind_citation_id(db_session, editor_user.id)

    assert next_id == partial.id


def test_next_blind_citation_excludes_ones_this_user_already_labeled(db_session: Session, seed, sample_prompt: Prompt, editor_user: User):
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt)
    db_session.add(VerificationLabel(citation_id=citation.id, user_id=editor_user.id, verdict="supported", mode="blind"))
    db_session.commit()

    assert _next_blind_citation_id(db_session, editor_user.id) is None


def test_next_blind_citation_offers_the_same_citation_to_a_different_user(db_session: Session, seed, sample_prompt: Prompt, editor_user: User, admin_user: User):
    """A second independent labeler must still be offered a citation the first one already

    labeled (T15's "~20 z nich nezávisle druhý člověk").
    """
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt)
    db_session.add(VerificationLabel(citation_id=citation.id, user_id=editor_user.id, verdict="supported", mode="blind"))
    db_session.commit()

    assert _next_blind_citation_id(db_session, admin_user.id) == citation.id


def test_next_blind_citation_is_none_without_any_llm_judged_citation(db_session: Session, editor_user: User):
    assert _next_blind_citation_id(db_session, editor_user.id) is None


def test_next_blind_citation_excludes_unverifiable_llm_checks(db_session: Session, seed, sample_prompt: Prompt, editor_user: User):
    """`check_type='llm'` rows whose OWN verdict is `unverifiable` (the source capture failed

    before the LLM was ever asked anything) must never be offered for blind labeling — there is
    no judgement there to agree or disagree with (found manually, 2026-09-29, against real data).
    """
    _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="unverifiable")

    assert _next_blind_citation_id(db_session, editor_user.id) is None


# --- GET /verification/label — blind, never leaks the LLM verdict -------------------------------


def test_label_page_shows_claim_and_passages_but_never_the_llm_verdict(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, verification = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_contradicted")

    response = authed_client.get("/verification/label")

    assert response.status_code == 200
    body = response.text
    assert "Acme is known for reliability." in body
    # "Contradicted" itself is expected — it's one of the four verdict-choice BUTTONS every blind
    # page shows regardless of the underlying citation (see human_verdicts in the render context).
    # What must never appear is the LLM's own raw verdict value, reason, or quote for THIS one.
    assert "llm_contradicted" not in body
    assert verification.llm_reason not in body
    assert f'value="{citation.id}"' in body


def test_label_page_shows_empty_state_when_nothing_is_eligible(authed_client: TestClient, db_session: Session):
    response = authed_client.get("/verification/label")

    assert response.status_code == 200
    assert "Nothing left to label" in response.text


def test_label_page_is_forbidden_for_a_viewer(client: TestClient, editor_user: User, viewer_user: User):
    client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    client.post("/auth/login", data={"username": viewer_user.email, "password": TEST_USER_PASSWORD})

    assert client.get("/verification/label").status_code == 403


# --- POST /verification/label --------------------------------------------------------------------


def test_submit_blind_label_records_a_row_and_redirects_to_the_next_one(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt)

    response = authed_client.post("/verification/label", data={"citation_id": citation.id, "verdict": "supported", "note": "looks right"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/verification/label"
    label = db_session.scalar(select(VerificationLabel).where(VerificationLabel.citation_id == citation.id))
    assert label.mode == "blind"
    assert label.verdict == "supported"
    assert label.note == "looks right"
    assert label.agrees_with_verification_id is None


def test_submit_blind_label_rejects_an_unrecognized_verdict(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt)

    response = authed_client.post("/verification/label", data={"citation_id": citation.id, "verdict": "definitely_true"})

    assert response.status_code == 400


def test_submit_blind_label_404s_for_an_unknown_citation(authed_client: TestClient):
    response = authed_client.post("/verification/label", data={"citation_id": 999999, "verdict": "supported"})

    assert response.status_code == 404


# --- POST /runs/{id}/citations/{id}/review -------------------------------------------------------


def test_review_agree_copies_the_llm_verdict_in_human_vocabulary(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, verification = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_partial")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    response = authed_client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "true"}, follow_redirects=False)

    assert response.status_code == 303
    label = db_session.scalar(select(VerificationLabel).where(VerificationLabel.citation_id == citation.id))
    assert label.mode == "review"
    assert label.verdict == "partially_supported"
    assert label.agrees_with_verification_id == verification.id


def test_review_disagree_with_a_correction_records_it(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, verification = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    authed_client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "false", "verdict": "not_supported"}, follow_redirects=False)

    label = db_session.scalar(select(VerificationLabel).where(VerificationLabel.citation_id == citation.id))
    assert label.verdict == "not_supported"
    assert label.agrees_with_verification_id == verification.id


def test_review_disagree_without_a_correction_records_null_verdict(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, verification = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    authed_client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "false"}, follow_redirects=False)

    label = db_session.scalar(select(VerificationLabel).where(VerificationLabel.citation_id == citation.id))
    assert label.verdict is None
    assert label.mode == "review"


def test_review_404s_when_the_citation_has_no_llm_verdict_yet(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    run = Run(prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, trigger_type="manual", status="success")
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="...", has_citations=True)
    db_session.add(raw)
    db_session.flush()
    citation = Citation(raw_response_id=raw.id, source_url="https://example.com/x", source_domain="example.com", citation_position=0)
    db_session.add(citation)
    db_session.commit()

    response = authed_client.post(f"/runs/{run.id}/citations/{citation.id}/review", data={"agree": "true"})

    assert response.status_code == 404


def test_review_is_forbidden_for_a_viewer(client: TestClient, editor_user: User, viewer_user: User, db_session: Session, seed, sample_prompt: Prompt):
    client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt)
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))
    client.post("/auth/login", data={"username": viewer_user.email, "password": TEST_USER_PASSWORD})

    assert client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "true"}).status_code == 403


# --- run detail integration ----------------------------------------------------------------------


def test_run_detail_shows_review_controls_for_an_llm_verdict(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    response = authed_client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert f'action="/runs/{run_id}/citations/{citation.id}/review"' in response.text


def test_run_detail_shows_a_persistent_confirmation_after_reviewing(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    """found manually, 2026-09-29: a plain POST/303 reloads the run detail page looking

    identical, so without some persistent state a click looked like it silently did nothing.
    """
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    before = authed_client.get(f"/runs/{run_id}")
    assert "You reviewed this" not in before.text

    authed_client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "false", "verdict": "not_supported"}, follow_redirects=False)

    after = authed_client.get(f"/runs/{run_id}")
    assert "You reviewed this: Not supported" in after.text


def test_run_detail_confirmation_shows_disagreed_with_no_correction(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))

    authed_client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "false"}, follow_redirects=False)

    response = authed_client.get(f"/runs/{run_id}")
    assert "Disagreed (no correction given)" in response.text


def test_run_detail_confirmation_only_reflects_this_users_own_latest_review(
    client: TestClient, db_session: Session, seed, sample_prompt: Prompt, editor_user: User, admin_user: User
):
    """A different user's review must never show up as "your" confirmation, and only the CALLER's

    own newest review (not an older one) should be reflected.
    """
    client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    citation, _ = _make_llm_judged_citation(db_session, seed, sample_prompt, verdict="llm_supported")
    run_id = db_session.scalar(select(RawResponse.run_id).where(RawResponse.id == citation.raw_response_id))
    client.post(f"/runs/{run_id}/citations/{citation.id}/review", data={"agree": "true"}, follow_redirects=False)

    client.post("/auth/login", data={"username": admin_user.email, "password": TEST_USER_PASSWORD})
    response = client.get(f"/runs/{run_id}")

    assert "You reviewed this" not in response.text
