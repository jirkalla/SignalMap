"""Run trigger flow (docs/REQUIREMENTS.md FR-7..FR-16), against FakeAdapter — never the real Gemini API."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterCitation, RawResponsePayload
from app.models import AIModel, AnalysisResult, Citation, Persona, Prompt, RawResponse, Run, SearchQuery
from tests.fake_adapter import FakeAdapter


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

    monkeypatch.setattr("app.routers.runs.get_runner", _boom)

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
