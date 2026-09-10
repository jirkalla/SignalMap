"""Run trigger flow (docs/REQUIREMENTS.md FR-7..FR-16), against FakeAdapter — never the real Gemini API."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import AdapterCitation, RawResponsePayload
from app.models import AnalysisResult, Citation, Prompt, RawResponse, Run, SearchQuery
from tests.fake_adapter import FakeAdapter


def test_successful_run_stores_run_raw_response_and_citations(client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Acme is known for reliability."},
        rendered_text="Acme is known for reliability.",
        has_citations=True,
        citations=[
            AdapterCitation(source_url="https://example.com/a", source_title="A", source_domain="example.com", citation_position=0)
        ],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
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
    client: TestClient, db_session: Session, seed, sample_prompt: Prompt
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

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    search_queries = db_session.scalars(
        select(SearchQuery).where(SearchQuery.raw_response_id == raw_response.id).order_by(SearchQuery.query_position)
    ).all()
    assert [q.query_text for q in search_queries] == ["first query", "second query"]
    assert [q.query_position for q in search_queries] == [0, 1]

    detail_response = client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "first query" in detail_response.text
    assert "second query" in detail_response.text


def test_successful_run_without_search_queries_shows_empty_state(
    client: TestClient, db_session: Session, seed, sample_prompt: Prompt
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

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    search_queries = db_session.scalars(select(SearchQuery).where(SearchQuery.raw_response_id == raw_response.id)).all()
    assert search_queries == []

    detail_response = client.get(f"/runs/{run_id}")
    assert detail_response.status_code == 200
    assert "did not issue any search queries" in detail_response.text


def test_failed_run_records_error_status_and_message(client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.error_to_raise = RuntimeError("simulated provider failure")

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "error"
    assert run.error_message == "simulated provider failure"

    assert db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id)) is None


def test_successful_run_against_the_anthropic_model(client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
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

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id},
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


def test_failed_run_against_the_anthropic_model_records_error(client: TestClient, db_session: Session, seed, sample_prompt: Prompt):
    FakeAdapter.error_to_raise = RuntimeError("Country code XX is not supported.")

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["anthropic_model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "error"
    assert run.error_message == "Country code XX is not supported."


def test_successful_run_stores_a_mention_visibility_analysis_result(
    client: TestClient, db_session: Session, seed, sample_prompt: Prompt
):
    """docs/TASKS_PHASE3.md design decision 7 — analysis runs automatically after a successful

    run. sample_prompt's client is named "Test Client" (tests/conftest.py), so a rendered
    answer that literally contains that name gives a predictable, assertable output.
    """
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "Test Client is known for reliability."},
        rendered_text="Test Client is known for reliability.",
        has_citations=False,
        citations=[],
        token_usage={"input_tokens": 10, "output_tokens": 5},
    )

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id))
    results = db_session.scalars(select(AnalysisResult).where(AnalysisResult.raw_response_id == raw_response.id)).all()

    assert len(results) == 1
    result = results[0]
    assert result.analysis_skill_id == seed["analysis_skill"].id
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


def test_analysis_engine_failure_never_fails_the_run(
    client: TestClient, db_session: Session, seed, sample_prompt: Prompt, monkeypatch
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

    response = client.post(
        f"/prompts/{sample_prompt.id}/runs",
        data={"model_id": seed["model"].id, "market_id": seed["market"].id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[-1])

    run = db_session.get(Run, run_id)
    assert run.status == "success"
    assert db_session.scalar(select(RawResponse).where(RawResponse.run_id == run_id)) is not None
    assert db_session.scalars(select(AnalysisResult).where(AnalysisResult.raw_response_id == run.raw_response.id)).all() == []
