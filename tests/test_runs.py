"""Run trigger flow (docs/REQUIREMENTS.md FR-7..FR-16), against FakeAdapter — never the real Gemini API."""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterCitation, RawResponsePayload
from app.models import AIModel, AnalysisResult, Citation, Persona, Prompt, RawResponse, Run, SearchQuery
from app.models.verification import CitationVerification, SourceDocument, SourceText, VerificationJob
from tests.conftest import TEST_USER_PASSWORD
from tests.fake_adapter import FakeAdapter

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_trigger_run_rejected_for_inactive_prompt(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """`Prompt.is_active` documents itself as "offer this prompt for new runs or not" but was
    never actually enforced anywhere — this is the fix. An inactive prompt is rejected (409)
    before any adapter call, and no Run row is created.
    """
    sample_prompt.is_active = False
    db_session.commit()

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert db_session.scalar(select(Run).where(Run.prompt_id == sample_prompt.id)) is None


def test_trigger_run_rejected_for_inactive_model(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """Code-review fix (2026-09-14) — `trigger_run` used to check `has_adapter(...)` for the
    submitted model but never `model.is_active`, so a model an admin deactivated (e.g. to stop
    further spend) could still be triggered by anyone who had its id. No Run row is created.
    """
    seed["model"].is_active = False
    db_session.commit()

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert db_session.scalar(select(Run).where(Run.prompt_id == sample_prompt.id)) is None


def test_trigger_run_rejected_when_client_daily_quota_exceeded(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_SCHEDULER.md T10, design decision 26 — the manual trigger enforces the exact

    same daily cap the scheduler does (app/services/run_execution.py's check_daily_quota), so a
    client can't be capped for the worker but still spend unattended through the manual path. No
    Run row is created for the rejected attempt.
    """
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 0
    db_session.commit()

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert db_session.scalar(select(Run).where(Run.prompt_id == sample_prompt.id)) is None


def test_trigger_run_reports_unrelated_integrity_errors_distinctly(
    monkeypatch, authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A concurrent FK violation (e.g. the model/market/persona row being deleted by someone
    else between trigger_run's own db.get() checks and its commit) must not be misreported as
    "run already pending" — only the specific partial unique index backstopping that race maps
    to that message; any other IntegrityError surfaces as a distinct, logged failure instead
    (code-review fix, 2026-09-15: the previous bare `except IntegrityError` caught both alike
    and silently discarded the real exception).

    Simulates the race by making the run's own `db.commit()` raise a synthetic IntegrityError
    carrying a constraint name other than `idx_runs_one_pending_per_prompt_model` — reproducing
    a real FK violation end-to-end would require deleting a referenced row mid-request, which
    isn't reachable through a single synchronous test client call.
    """

    class _FakeDiag:
        constraint_name = "runs_model_id_fkey"

    class _FakeOrig:
        diag = _FakeDiag()

    original_commit = Session.commit
    state = {"raised": False}

    def fake_commit(self, *args, **kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise IntegrityError("INSERT INTO runs ...", {}, _FakeOrig())
        return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", fake_commit)

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "unexpected database error" in response.text.lower()
    assert "already in progress" not in response.text.lower()
    assert db_session.scalar(select(Run).where(Run.prompt_id == sample_prompt.id)) is None


def test_pending_run_unique_index_rejects_a_second_pending_row(
    db_session: Session, seed, sample_prompt: Prompt
):
    """Code-review fix (2026-09-14), migration 0024 — `trigger_run`'s pending-run guard is a
    plain SELECT-then-INSERT with a TOCTOU race window; this partial unique index is the
    database-level backstop. Bypasses the application guard entirely (two direct ORM inserts)
    to prove the constraint itself — not `trigger_run`'s catch of it — is what closes the race.
    """
    first = Run(
        prompt_id=sample_prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="pending",
    )
    db_session.add(first)
    db_session.commit()

    second = Run(
        prompt_id=sample_prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="pending",
    )
    db_session.add(second)
    try:
        db_session.commit()
        assert False, "expected IntegrityError from idx_runs_one_pending_per_prompt_model"
    except IntegrityError:
        db_session.rollback()

    remaining_pending_id = db_session.scalar(
        select(Run.id)
        .where(Run.prompt_id == sample_prompt.id, Run.model_id == seed["model"].id, Run.status == "pending")
    )
    assert remaining_pending_id == first.id


def test_trigger_run_rejected_while_one_is_already_pending(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T9 — a second trigger on the same prompt AND
    model is rejected (409), not silently started, while a Run for it is still status='pending'.

    No-regression check for docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T1: the guard's WHERE
    clause was widened to also match on model_id (see the sibling test below for the case that
    change was meant to unblock), but triggering the *same* model twice must still 409 exactly
    as before.
    """
    pending = Run(
        prompt_id=sample_prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="pending",
    )
    db_session.add(pending)
    db_session.commit()

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 409
    runs = db_session.scalars(select(Run).where(Run.prompt_id == sample_prompt.id)).all()
    assert len(runs) == 1  # only the pre-seeded pending run — no second Run was created
    assert runs[0].id == pending.id


def test_trigger_run_allowed_for_different_model_while_another_is_pending(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_BULK_IMPORT_MULTI_MODEL.md BIM-T1 — the pending-run guard is scoped to
    (prompt_id, model_id), not prompt_id alone, so triggering a *different* model for the same
    prompt must succeed even while another model's Run is still 'pending' — this is what lets
    BIM-T2's multi-model checkboxes fire several models in parallel without tripping the guard
    on each other.
    """
    pending = Run(
        prompt_id=sample_prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="pending",
    )
    db_session.add(pending)
    db_session.commit()

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "from another model"},
        rendered_text="from another model",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 1, "output_tokens": 1},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={
            "model_id": seed["anthropic_model"].id,
            "market_id": seed["market"].id,
            "persona_id": seed["persona"].id,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    runs = db_session.scalars(select(Run).where(Run.prompt_id == sample_prompt.id)).all()
    assert len(runs) == 2  # the pre-seeded pending run plus the new one on the other model
    assert {r.model_id for r in runs} == {seed["model"].id, seed["anthropic_model"].id}


def test_trigger_run_succeeds_again_once_the_previous_one_finished(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """The pending-run guard must not permanently lock a prompt — once a run's status has moved
    past 'pending' (success or error), triggering another must work normally.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "first"},
        rendered_text="first",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 1, "output_tokens": 1},
    )
    first_response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    assert first_response.status_code == 303

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "second"},
        rendered_text="second",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 1, "output_tokens": 1},
    )
    second_response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    assert second_response.status_code == 303

    runs = db_session.scalars(select(Run).where(Run.prompt_id == sample_prompt.id)).all()
    assert len(runs) == 2
    assert all(r.status == "success" for r in runs)


def test_successful_run_stores_run_raw_response_and_citations(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=True,
        citations=[
            AdapterCitation(source_url="https://example.com/a", source_title="A", source_domain="example.com", citation_position=0)
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "success"
    assert run.error_message is None

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    assert raw_response is not None
    assert raw_response.rendered_text == "Acme is known for reliability."
    assert raw_response.has_citations is True

    citations = db_session.scalars(select(Citation).where(Citation.raw_response_id == raw_response.id)).all()
    assert len(citations) == 1
    assert citations[0].source_domain == "example.com"


def test_successful_run_stores_and_displays_the_cited_claim_with_its_offsets(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_GEMINI_CITATIONS.md GC-T5 — the answer-side half of a citation.

    Shaped like what the Gemini and OpenAI adapters produce: the claim plus the
    offsets locating it, and no source passage. The detail page must show the
    claim label and not the source-passage one.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://example.com/a",
                source_title="A",
                source_domain="example.com",
                citation_position=0,
                cited_answer_span="Acme is known for reliability.",
                answer_span_start=0,
                answer_span_end=30,
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))
    assert citation.cited_answer_span == "Acme is known for reliability."
    assert citation.answer_span_start == 0
    assert citation.answer_span_end == 30
    assert citation.source_passage is None

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Cited claim" in detail_response.text
    assert "Source passage" not in detail_response.text


def test_successful_run_stores_and_displays_the_source_passage(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_GEMINI_CITATIONS.md GC-T5 — the source-side half of a citation.

    Shaped like what the Anthropic adapter produces: a passage quoted from the
    page, no answer span and no offsets (its API exposes none). The detail page
    must show the source-passage label and not the claim one.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://example.com/a",
                source_title="A",
                source_domain="example.com",
                citation_position=0,
                source_passage="Acme has topped reliability rankings since 2019.",
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))
    assert citation.source_passage == "Acme has topped reliability rankings since 2019."
    assert citation.cited_answer_span is None
    assert citation.answer_span_start is None
    assert citation.answer_span_end is None

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Source passage" in detail_response.text
    assert "Cited claim" not in detail_response.text


def test_openai_run_detail_shows_the_derived_sentence_not_the_raw_link_marker(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CITATION_VERIFICATION.md T2 — before this, the detail page showed OpenAI's

    `cited_answer_span` (the raw `([domain](url))` link marker) as the "Cited claim". It must
    now show the sentence `derive_claim` recovers from before that marker instead.
    """
    sentence = "Acme is a reliable brand."
    marker = "([acme.com](https://acme.com/about?utm_source=openai))"
    rendered_text = f"{sentence} {marker}"
    marker_start = len(sentence) + 1
    marker_end = marker_start + len(marker)

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"output": [{"type": "message", "content": [{"type": "output_text", "text": rendered_text}]}]},
        rendered_text=rendered_text,
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://acme.com/about?utm_source=openai",
                source_title="Acme",
                source_domain="acme.com",
                citation_position=0,
                cited_answer_span=marker,
                answer_span_start=marker_start,
                answer_span_end=marker_end,
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["openai_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Cited claim" in detail_response.text
    assert f"&ldquo;{sentence}&rdquo;" in detail_response.text
    assert f"&ldquo;{marker}&rdquo;" not in detail_response.text


def test_anthropic_run_detail_now_shows_the_cited_claim(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CITATION_VERIFICATION.md T2 — before this, an Anthropic citation never showed a

    "Cited claim" at all (`cited_answer_span` is permanently None for Anthropic — see Citation's
    docstring). The detail page must now derive and show one from the response block the citation
    is attached to, alongside the "Source passage" it already showed.
    """
    rendered_text = "Acme is known for reliability."
    raw_payload = {
        "content": [
            {
                "type": "text",
                "text": rendered_text,
                "citations": [
                    {
                        "url": "https://example.com/a",
                        "title": "A",
                        "cited_text": "Acme has topped reliability rankings since 2019.",
                    }
                ],
            }
        ]
    }

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload=raw_payload,
        rendered_text=rendered_text,
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://example.com/a",
                source_title="A",
                source_domain="example.com",
                citation_position=0,
                source_passage="Acme has topped reliability rankings since 2019.",
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Cited claim" in detail_response.text
    assert "Source passage" in detail_response.text
    assert f"&ldquo;{rendered_text}&rdquo;" in detail_response.text


def test_successful_run_stores_and_displays_search_queries_in_order(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_SEARCH_QUERIES.md SQ-T4 — persistence and display, via FakeAdapter."""
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=False,
        citations=[],
        search_queries=["first query", "second query"],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    search_queries = db_session.scalars(
        select(SearchQuery).where(SearchQuery.raw_response_id == raw_response.id).order_by(SearchQuery.query_position)
    ).all()
    assert [q.query_text for q in search_queries] == ["first query", "second query"]
    assert [q.query_position for q in search_queries] == [0, 1]

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "first query" in detail_response.text
    assert "second query" in detail_response.text


def test_successful_run_without_search_queries_shows_empty_state(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A run whose payload has no search_queries (default empty list) must render the empty

    state, never a crash — same guarantee citations already have.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    search_queries = db_session.scalars(select(SearchQuery).where(SearchQuery.raw_response_id == raw_response.id)).all()
    assert search_queries == []

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "did not issue any search queries" in detail_response.text


def test_successful_run_against_a_model_with_no_web_search_shows_dedicated_explanation(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """NP-T3: a run against a model with `supports_web_search = False` (e.g. DeepSeek) gets a
    dedicated explanation for why there are no citations, distinct from the generic empty state
    every other (search-capable) provider's occasional citation-less run shows.
    """
    no_search_model = AIModel(
        provider_id=seed["provider"].id, model_name="no-search-test-model", capability_tier="economy", supports_web_search=False
    )
    db_session.add(no_search_model)
    db_session.commit()
    db_session.refresh(no_search_model)

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is a solid company."},
        rendered_text="Acme is a solid company.",
        has_citations=False,
        citations=[],
        token_usage={"prompt_tokens": 10, "completion_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": no_search_model.id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "no web search" in detail_response.text
    assert "No citations were returned for this run." not in detail_response.text


def test_failed_run_records_error_status_and_message(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.error_to_raise = RuntimeError("simulated provider failure")

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "error"
    assert run.error_message == "simulated provider failure"

    assert db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id)) is None


def test_successful_run_against_the_anthropic_model(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    """Same flow as the Gemini test above, against seed['anthropic_model'] — proves the run

    path is provider-agnostic (P2-T4), still via FakeAdapter, never the real Anthropic API.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"content": [{"type": "text", "text": "Acme is a reliable brand."}]},
        rendered_text="Acme is a reliable brand.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 12, "output_tokens": 6},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "success"
    assert run.model_id == seed["anthropic_model"].id

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    assert raw_response is not None
    assert raw_response.rendered_text == "Acme is a reliable brand."
    assert raw_response.has_citations is False


def test_failed_run_against_the_anthropic_model_records_error(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.error_to_raise = RuntimeError("Country code XX is not supported.")

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "error"
    assert run.error_message == "Country code XX is not supported."


def test_successful_run_against_the_openai_model(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    """Same flow as the Gemini/Anthropic tests above, against seed['openai_model'] — proves the

    run path is provider-agnostic for the third provider too (CPH-T7), still via FakeAdapter,
    never the real OpenAI API.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"output": [{"type": "message", "content": [{"type": "output_text", "text": "Acme is reliable."}]}]},
        rendered_text="Acme is reliable.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 8, "output_tokens": 4},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["openai_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "success"
    assert run.model_id == seed["openai_model"].id

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    assert raw_response is not None
    assert raw_response.rendered_text == "Acme is reliable."
    assert raw_response.has_citations is False


def test_failed_run_against_the_openai_model_records_error(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.error_to_raise = RuntimeError("Incorrect API key provided.")

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["openai_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "error"
    assert run.error_message == "Incorrect API key provided."


def test_run_with_overridden_persona_records_it_in_request_payload(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T5 — a run explicitly triggered with a
    non-default persona records that persona's label in request_payload, not the default's.
    """
    manager_persona = Persona(label="manager", is_default=False)
    db_session.add(manager_persona)
    db_session.commit()
    db_session.refresh(manager_persona)

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": manager_persona.id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.persona_id == manager_persona.id
    assert run.request_payload["persona"] == "manager"


def test_successful_run_stores_a_mention_visibility_analysis_result(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_PHASE3.md design decision 7 — analysis runs automatically after a successful

    run. sample_prompt's authed_client is named "Test Client" (tests/conftest.py), so a rendered
    answer that literally contains that name gives a predictable, assertable output.

    Both mention_visibility and competitive_visibility run for every successful run (seed fixture
    activates both, docs/TASKS_PHASE5.md P5-T3) — this test asserts on the mention_visibility row
    specifically; test_successful_run_also_stores_a_competitive_visibility_analysis_result below
    covers the second one.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Test Client is known for reliability."},
        rendered_text="Test Client is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    results = db_session.scalars(select(AnalysisResult).where(AnalysisResult.raw_response_id == raw_response.id)).all()
    assert len(results) == 2

    result = next(r for r in results if r.analysis_skill_id == seed["analysis_skill"].id)
    assert result.skill_version == 1
    assert result.output == {
        "text_mentioned": True,
        "mention_count": 1,
        "first_mention_position": 0,
        "matched_terms": ["Test Client"],
        "match_spans": [[0, 11]],
        "cited": False,
        "cited_domains": [],
    }


def test_successful_run_also_stores_a_competitive_visibility_analysis_result(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_PHASE5.md P5-T6 — competitive_visibility runs automatically alongside
    mention_visibility, with no signature change to _run_active_analysis_skills (design decision
    7). sample_prompt's authed_client has no tracked_entities, so the entities array holds only the
    authed_client's own row — still a real, assertable output, not skipped.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Test Client is known for reliability."},
        rendered_text="Test Client is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    results = db_session.scalars(select(AnalysisResult).where(AnalysisResult.raw_response_id == raw_response.id)).all()

    result = next(r for r in results if r.analysis_skill_id == seed["competitive_visibility_skill"].id)
    assert result.skill_version == 1
    assert result.output["share_of_voice"] == 1.0
    assert result.output["position"] == 1
    assert result.output["entities"] == [
        {
            "name": "Test Client",
            "is_own_client": True,
            "mentioned": True,
            "mention_count": 1,
            "first_position": 0,
            "cited": False,
            "cited_domains": [],
        }
    ]


def test_analysis_engine_failure_never_fails_the_run(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt, monkeypatch
):
    """docs/TASKS_PHASE3.md design decision 7 — a broken analysis skill must not roll back or

    invalidate the evidence a run already committed (Run.status, RawResponse).
    """

    def _boom(skill_key: str):
        raise RuntimeError("simulated analysis failure")

    monkeypatch.setattr("app.services.run_execution.get_runner", _boom)

    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Test Client is known for reliability."},
        rendered_text="Test Client is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "success"
    assert db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id)) is not None
    assert db_session.scalars(select(AnalysisResult).where(AnalysisResult.raw_response_id == run.raw_response.id)).all() == []


def test_viewer_cannot_trigger_a_run(viewer_client: TestClient, seed, sample_prompt: Prompt):
    """docs/TASKS_PHASE6.md P6-T6 — viewer is read-only everywhere; require_role() is the actual
    enforcement, hiding the "Run" button in the template is UX only.
    """
    response = viewer_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_viewer_cannot_export_a_run(viewer_client: TestClient):
    """403 fires from require_role() before the route body even looks the run up — true
    regardless of whether the run id exists, so this needs no real run fixture.
    """
    response = viewer_client.get("/runs/999999/export")

    assert response.status_code == 403


# --- T9: citation verification UI (docs/TASKS_CITATION_VERIFICATION.md) ------------------------


def _trigger_citation_run(authed_client: TestClient, seed, sample_prompt: Prompt) -> tuple[int, RawResponse, Citation]:
    """A run with one citation shaped for derive_claim's offset-based method (same payload shape

    as test_successful_run_stores_and_displays_the_cited_claim_with_its_offsets above) — every T9
    test below needs a highlighted claim to check, not just a bare citation.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://example.com/a",
                source_title="A",
                source_domain="example.com",
                citation_position=0,
                cited_answer_span="Acme is known for reliability.",
                answer_span_start=0,
                answer_span_end=30,
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])
    return run_id, response, None  # citation/raw_response looked up by the caller, needs db_session


def test_verification_summary_shows_pending_state_before_capture(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CITATION_VERIFICATION.md T9 — a fresh run's capture job (queued by run_execution,

    T5) hasn't been processed yet, so the summary card must show the "waiting for capture" message
    instead of verdict filter chips that would all be empty/misleading.
    """
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Sources for this run haven&#39;t been captured yet" in detail_response.text or "haven't been captured yet" in detail_response.text
    assert 'id="verdict-filters"' not in detail_response.text


def test_verification_no_citations_shows_empty_state(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    """No citations at all (e.g. a model with no web search) — the summary card must say so

    rather than showing an empty filter row or a misleading "0 captured" line.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "No sources here."}, rendered_text="No sources here.", has_citations=False, citations=[],
        token_usage={"input_tokens": 5, "output_tokens": 3},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Nothing to verify: this run has no citations." in detail_response.text


def test_verification_shows_verdict_badge_claim_highlight_and_evidence_after_capture(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """Once a CitationVerification row exists and the capture job is done, the detail page must

    show: the verdict badge (summary chip + citation-list badge), the claim underlined with a
    citation-number button (data-claim/data-tone), and an evidence block with the matched context,
    location, and an HTTP status line.
    """
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))

    capture_job = db_session.scalar(
        select(VerificationJob).where(VerificationJob.raw_response_id == raw_response.id, VerificationJob.kind == "capture")
    )
    capture_job.status = "done"
    capture_job.finished_at = NOW

    source_text = "On our review page: Acme has been rated highly for reliability since 2019. Read more."
    text_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    db_session.add(SourceText(sha256=text_sha256, text=source_text, chars=len(source_text)))
    document = SourceDocument(
        requested_url=citation.source_url, method="live", http_status=200, text_sha256=text_sha256,
        duration_ms=120, fetched_at=NOW, verifier_version="1.0",
    )
    db_session.add(document)
    db_session.flush()

    matched = "Acme has been rated highly for reliability since 2019."
    match_start = source_text.index(matched)
    db_session.add(
        CitationVerification(
            citation_id=citation.id, source_document_id=document.id, claim_text="Acme is known for reliability.",
            claim_method="position", check_type="quote", verdict="verified_exact", similarity=1,
            matched_text=matched, match_start=match_start, match_end=match_start + len(matched),
            location={"headings": ["Reviews"]}, verifier_version="1.0",
        )
    )
    db_session.commit()

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    body = detail_response.text
    assert "Verified &middot; exact" in body or "Verified · exact" in body or "Verified" in body
    assert f'data-evidence="{citation.id}"' in body
    assert f'data-tone="emerald"' in body
    assert "data-claim=" in body
    assert "Reviews" in body
    assert "1 citations" in body and "1 URLs" in body and "1 captured" in body
    assert "HTTP 200" in body
    # Regression guard (found manually, 2026-09-29): partials/verification.html's click-to-show-
    # evidence script is defined as its own macro (verification_assets) precisely so it actually
    # gets rendered — `{% from ... import %}` alone never emits a template's top-level body.
    assert "function showEvidence" in body


def test_verification_resolved_url_shown_as_plain_text_not_a_link(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_CITATION_VERIFICATION.md design decision 6 — Gemini's own redirect link must

    stay the clickable "source_url" link; the address it resolves to may only ever appear as plain
    text, never as a second, competing link (Google's terms forbid modifying/substituting it).
    """
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))
    capture_job = db_session.scalar(
        select(VerificationJob).where(VerificationJob.raw_response_id == raw_response.id, VerificationJob.kind == "capture")
    )
    capture_job.status = "done"

    resolved = "https://reviews.example.com/acme-reliability"
    document = SourceDocument(
        requested_url=citation.source_url, final_url=resolved, method="live", http_status=200,
        fetched_at=NOW, verifier_version="1.0",
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        CitationVerification(
            citation_id=citation.id, source_document_id=document.id, claim_text="Acme is known for reliability.",
            check_type="quote", verdict="unverifiable", reason="no_checkable_text", verifier_version="1.0",
        )
    )
    db_session.commit()

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    body = detail_response.text
    assert "Resolves to:" in body
    assert resolved in body
    assert f'href="{resolved}"' not in body


def test_verification_collapsed_section_shows_ctrl_f_warning(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A match found inside a collapsed `<details>`/accordion section must warn that the browser's

    own text search won't find it while collapsed — otherwise a user trying to verify by hand with
    Ctrl+F would wrongly conclude the citation is wrong.
    """
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))
    capture_job = db_session.scalar(
        select(VerificationJob).where(VerificationJob.raw_response_id == raw_response.id, VerificationJob.kind == "capture")
    )
    capture_job.status = "done"

    document = SourceDocument(requested_url=citation.source_url, method="live", http_status=200, fetched_at=NOW, verifier_version="1.0")
    db_session.add(document)
    db_session.flush()
    db_session.add(
        CitationVerification(
            citation_id=citation.id, source_document_id=document.id, claim_text="Acme is known for reliability.",
            check_type="quote", verdict="verified_exact", similarity=1,
            location={"collapsed": True, "collapsed_title": "Customer reviews"}, verifier_version="1.0",
        )
    )
    db_session.commit()

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "Customer reviews" in detail_response.text
    assert "won&#39;t find it while it&#39;s collapsed" in detail_response.text or "won't find it while it's collapsed" in detail_response.text


def test_verification_shows_verdicts_with_no_capture_job_row_at_all(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A handful of pre-T5 runs have citation_verifications but no verification_jobs row at all

    (backfilled/verified before the automatic post-run capture enqueue existed). The page must
    still show their verdicts — not the "check back in a minute" pending message, which for these
    runs would never resolve (found manually, 2026-09-29, walking through run 108).
    """
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response.id))
    # No VerificationJob at all — delete the one the run's own trigger flow enqueued, simulating a
    # pre-T5 run where that enqueue never happened.
    for job in db_session.scalars(select(VerificationJob).where(VerificationJob.raw_response_id == raw_response.id)).all():
        db_session.delete(job)

    document = SourceDocument(requested_url=citation.source_url, method="live", http_status=200, fetched_at=NOW, verifier_version="1.0")
    db_session.add(document)
    db_session.flush()
    db_session.add(
        CitationVerification(
            citation_id=citation.id, source_document_id=document.id, claim_text="Acme is known for reliability.",
            check_type="quote", verdict="verified_exact", similarity=1, verifier_version="1.0",
        )
    )
    db_session.commit()

    detail_response = authed_client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    body = detail_response.text
    assert "haven't been captured yet" not in body
    assert 'id="verdict-filters"' in body
    assert "1 captured" in body


# --- "Verify citations" button (docs/TASKS_CITATION_VERIFICATION.md T13 point 2) ----------------


def test_verify_citations_button_shown_on_an_eligible_run(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)

    detail_response = authed_client.get(f"/runs/{run_id}")

    assert f'action="/runs/{run_id}/verify-citations"' in detail_response.text


def test_verify_citations_button_hidden_for_a_provider_with_no_llm_judge_path(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """Anthropic already gets the free quote check (T8) — the button only makes sense for

    OpenAI/Gemini citations, which have no source text to check without an LLM.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"content": [{"type": "text", "text": "Acme is reliable.", "citations": [{"url": "https://example.com/a", "title": "A", "cited_text": "..."}]}]},
        rendered_text="Acme is reliable.",
        has_citations=True,
        citations=[
            AdapterCitation(
                source_url="https://example.com/a", source_title="A", source_domain="example.com",
                citation_position=0, source_passage="Acme has been rated highly for reliability.",
            )
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    detail_response = authed_client.get(f"/runs/{run_id}")

    assert f'action="/runs/{run_id}/verify-citations"' not in detail_response.text


def test_verify_run_citations_htmx_request_returns_hx_redirect(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)

    response = authed_client.post(f"/runs/{run_id}/verify-citations", headers={"HX-Request": "true"}, follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/runs/{run_id}"


def test_verify_run_citations_plain_post_redirects(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)

    response = authed_client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{run_id}"


def test_verify_run_citations_enqueues_a_judge_job_requested_by_the_user(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))

    authed_client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False)

    job = db_session.scalar(
        select(VerificationJob).where(VerificationJob.raw_response_id == raw_response.id, VerificationJob.kind == "judge")
    )
    assert job is not None
    assert job.requested_by_user_id is not None


def test_verify_run_citations_409s_for_a_run_with_no_citations(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "No sources here."}, rendered_text="No sources here.", has_citations=False, citations=[],
        token_usage={"input_tokens": 5, "output_tokens": 3},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    assert authed_client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False).status_code == 409


def test_verify_run_citations_is_forbidden_for_a_viewer(client: TestClient, editor_user, viewer_user, db_session: Session, seed, sample_prompt: Prompt):
    """`viewer_client`/`authed_client` together share one underlying session (both log into the

    same `client` fixture) and end up authenticated as whichever is resolved last by pytest —
    editor, given this file's own parameter order elsewhere, which wouldn't actually prove
    anything about a viewer against an editor-or-admin route. Logging in explicitly, in the order
    this test actually needs, avoids that trap (same fix as tests/test_clients.py's equivalent).
    """
    client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    run_id, _, _ = _trigger_citation_run(client, seed, sample_prompt)
    client.post("/auth/login", data={"username": viewer_user.email, "password": TEST_USER_PASSWORD})

    assert client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False).status_code == 403


# --- "Verify citations" progress + duplicate guard ---------------------------------------------


def _raw_response_of(db_session: Session, run_id: int) -> RawResponse:
    return db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))


def _add_llm_verdict(db_session: Session, raw_response_id: int) -> CitationVerification:
    """One LLM verdict row on the response's first citation."""
    citation = db_session.scalar(select(Citation).where(Citation.raw_response_id == raw_response_id).order_by(Citation.id))
    verification = CitationVerification(citation_id=citation.id, check_type="llm", verdict="llm_supported", verifier_version="1.0")
    db_session.add(verification)
    db_session.commit()
    return verification


def _judge_jobs(db_session: Session, raw_response_id: int) -> list[VerificationJob]:
    return list(
        db_session.scalars(
            select(VerificationJob).where(VerificationJob.raw_response_id == raw_response_id, VerificationJob.kind == "judge")
        )
    )


def _add_judge_job(db_session: Session, raw_response_id: int, status: str) -> VerificationJob:
    job = VerificationJob(raw_response_id=raw_response_id, kind="judge", status=status, scheduled_for=datetime.now(timezone.utc))
    if status in ("done", "error"):
        job.finished_at = datetime.now(timezone.utc)
    db_session.add(job)
    db_session.commit()
    return job


@pytest.mark.parametrize("active_status", ["queued", "leased", "deferred"])
def test_verify_run_citations_ignores_a_click_while_a_judge_job_is_active(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt, active_status: str
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, active_status)

    response = authed_client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False)

    assert response.status_code == 303  # still redirects to the page that shows the progress
    assert len(_judge_jobs(db_session, raw_response.id)) == 1


@pytest.mark.parametrize("finished_status", ["done", "error"])
def test_verify_run_citations_enqueues_again_once_the_previous_job_finished(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt, finished_status: str
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, finished_status)

    authed_client.post(f"/runs/{run_id}/verify-citations", follow_redirects=False)

    assert len(_judge_jobs(db_session, raw_response.id)) == 2


def test_run_detail_shows_progress_instead_of_the_button_while_a_judge_job_is_active(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, "leased")

    page = authed_client.get(f"/runs/{run_id}").text

    assert f'action="/runs/{run_id}/verify-citations"' not in page
    assert f'hx-get="/runs/{run_id}/verify-status?poll=true"' in page
    assert 'hx-trigger="every 4s"' in page


def test_run_detail_shows_last_verified_and_the_button_after_a_done_job(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, "done")
    _add_llm_verdict(db_session, raw_response.id)

    page = authed_client.get(f"/runs/{run_id}").text

    assert f'action="/runs/{run_id}/verify-citations"' in page
    assert "hx-trigger" not in page.split('id="verify-status"', 1)[1].split("</div>", 1)[0]  # no polling once idle
    assert "Last verified" in page
    assert "&lt;time" not in page  # the local_time() markup must render as a <time> element, not as escaped text


def test_run_detail_shows_an_error_message_and_the_button_after_a_failed_job(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, "error")

    page = authed_client.get(f"/runs/{run_id}").text

    assert "The last verification failed" in page
    assert f'action="/runs/{run_id}/verify-citations"' in page


def test_verify_status_poll_returns_hx_refresh_once_the_job_finished(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    job = _add_judge_job(db_session, raw_response.id, "leased")

    still_running = authed_client.get(f"/runs/{run_id}/verify-status?poll=true")
    assert still_running.status_code == 200
    assert "HX-Refresh" not in still_running.headers
    assert 'hx-trigger="every 4s"' in still_running.text

    job.status = "done"
    job.finished_at = datetime.now(timezone.utc)
    db_session.commit()

    finished = authed_client.get(f"/runs/{run_id}/verify-status?poll=true")
    assert finished.status_code == 200
    assert finished.headers["HX-Refresh"] == "true"


def test_verify_status_without_poll_renders_the_idle_state_even_when_no_job_exists(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)

    response = authed_client.get(f"/runs/{run_id}/verify-status")

    assert response.status_code == 200
    assert f'action="/runs/{run_id}/verify-citations"' in response.text


def test_verify_status_409s_for_a_run_with_nothing_to_verify(authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "No sources here."}, rendered_text="No sources here.", has_citations=False, citations=[],
        token_usage={"input_tokens": 5, "output_tokens": 3},
    )
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id, "persona_id": seed["persona"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    assert authed_client.get(f"/runs/{run_id}/verify-status").status_code == 409


def test_a_viewer_sees_the_job_progress_but_no_verify_button(
    client: TestClient, editor_user, viewer_user, db_session: Session, seed, sample_prompt: Prompt
):
    client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    run_id, _, _ = _trigger_citation_run(client, seed, sample_prompt)
    raw_response = _raw_response_of(db_session, run_id)
    _add_judge_job(db_session, raw_response.id, "done")
    _add_llm_verdict(db_session, raw_response.id)
    client.post("/auth/login", data={"username": viewer_user.email, "password": TEST_USER_PASSWORD})

    page = client.get(f"/runs/{run_id}").text

    assert "Last verified" in page
    assert f'action="/runs/{run_id}/verify-citations"' not in page


# --- "Last verified" and polling details (docs/TASKS_CITATION_HARDENING.md T10) ----------------


def test_a_done_judge_job_that_judged_nothing_does_not_claim_the_run_was_verified(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """The job finishes `done` when no source was captured yet, having written no verdict at all."""
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    _add_judge_job(db_session, _raw_response_of(db_session, run_id).id, "done")

    page = authed_client.get(f"/runs/{run_id}").text

    assert "Last verified" not in page
    assert f'action="/runs/{run_id}/verify-citations"' in page  # and the button is still offered


def test_last_verified_follows_the_verdict_rows_even_without_any_judge_job(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """Verification that rode along inside an automatic capture job has no judge job at all."""
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    _add_llm_verdict(db_session, _raw_response_of(db_session, run_id).id)

    page = authed_client.get(f"/runs/{run_id}").text

    assert "Last verified" in page


def test_a_failed_job_after_an_earlier_verification_shows_both_lines(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    _add_llm_verdict(db_session, _raw_response_of(db_session, run_id).id)
    _add_judge_job(db_session, _raw_response_of(db_session, run_id).id, "error")

    page = authed_client.get(f"/runs/{run_id}").text

    assert "The last verification failed" in page
    assert "Last verified" in page


def test_a_job_waiting_out_a_retry_backoff_is_polled_less_often(
    authed_client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """A deferred job waits up to 25 minutes; polling it every 4 s would be ~375 requests per tab."""
    run_id, _, _ = _trigger_citation_run(authed_client, seed, sample_prompt)
    _add_judge_job(db_session, _raw_response_of(db_session, run_id).id, "deferred")

    page = authed_client.get(f"/runs/{run_id}").text

    assert 'hx-trigger="every 30s"' in page
    assert 'hx-trigger="every 4s"' not in page
