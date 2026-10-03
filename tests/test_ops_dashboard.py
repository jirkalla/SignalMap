"""Ops dashboard aggregation and access tests (docs/TASKS_OPS_DASHBOARD.md T4).

Fixture data is built directly against the ORM (same precedent as tests/test_dashboard.py) so each
run's started_at, trigger_type, triggered_by_user_id, and token_usage shape can be pinned exactly —
a real trigger_run/FakeAdapter flow can't express "this run was triggered by the Scheduler" or "this
run predates user-attribution tracking".

The shared `seed` fixture's models have no price components — every cost-related test here sets a
price explicitly on the specific model it uses, rather than changing the shared fixture (which
other test files rely on staying price-less).
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AIModel, AIModelPriceComponent, Client, Prompt, PromptSet, RawResponse, Run, User
from app.models.run import Citation
from app.models.verification import CitationVerification, SourceDocument, VerificationJob
from app.services.cost import average_llm_judge_cost_per_citation, client_month_to_date_spend, estimate_run_cost, load_price_components, prices_at


def _client_with_prompt_set(db_session: Session, name: str, slug: str) -> tuple[Client, PromptSet]:
    client_row = Client(name=name, slug=slug)
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name=f"{name} prompts")
    db_session.add(prompt_set)
    db_session.commit()
    db_session.refresh(client_row)
    db_session.refresh(prompt_set)
    return client_row, prompt_set


def _make_prompt(
    db_session: Session,
    prompt_set: PromptSet,
    market_id: int,
    text: str,
    *,
    topic: str | None = None,
    root_prompt_id: int | None = None,
    is_current_version: bool = True,
    version: int = 1,
) -> Prompt:
    prompt = Prompt(
        prompt_set_id=prompt_set.id,
        root_prompt_id=root_prompt_id,
        version=version,
        is_current_version=is_current_version,
        text=text,
        market_id=market_id,
        topic=topic,
    )
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return prompt


def _make_run(
    db_session: Session,
    prompt: Prompt,
    *,
    model_id: int,
    market_id: int,
    persona_id: int,
    started_at: datetime,
    status: str = "success",
    trigger_type: str = "manual",
    triggered_by_user_id: int | None = None,
    latency_ms: int | None = 100,
    error_message: str | None = None,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    token_usage_shape: str = "anthropic",
) -> Run:
    """One Run, plus (for a successful run) a RawResponse carrying a provider-shaped token_usage —
    'anthropic' shape (input_tokens/output_tokens, shared with OpenAI) by default, 'gemini' shape
    (prompt_token_count/candidates_token_count) on request, to exercise both COALESCE branches of
    `run_cost_sql_expr` at the aggregation level, not just cost.py's own unit tests.
    """
    run = Run(
        prompt_id=prompt.id,
        model_id=model_id,
        market_id=market_id,
        persona_id=persona_id,
        trigger_type=trigger_type,
        triggered_by_user_id=triggered_by_user_id,
        status=status,
        started_at=started_at,
        latency_ms=latency_ms,
        error_message=error_message,
    )
    db_session.add(run)
    db_session.flush()

    if status == "success":
        if token_usage_shape == "gemini":
            token_usage = {"prompt_token_count": input_tokens, "candidates_token_count": output_tokens}
        else:
            token_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        db_session.add(
            RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="...", token_usage=token_usage)
        )

    db_session.commit()
    return run


def _price(db_session: Session, model, cost_in: float, cost_out: float) -> None:
    """Insert 'input'/'output' AIModelPriceComponent rows for `model` — CC-4 repoints run costing
    at these instead of the old flat cost_per_1k_*_usd columns. `cost_in`/`cost_out` are kept as
    the old per-1k values this file's callers already pass (× 1000 to the per-1M unit the table
    actually stores — the same arithmetic identity the old cost_per_1k_* column implied, so every
    hardcoded expected cost already written against this fixture data still holds unchanged).

    `effective_from` is pinned a year back, comfortably before any run this file creates (the
    furthest back-dated run is 5 days) — run_cost_sql_expr/prices_at resolve the price effective
    AT the run's own started_at, not today's, so a component "effective" only from right now would
    never apply to a run dated in the past.
    """
    effective_from = datetime.now(timezone.utc) - timedelta(days=365)
    db_session.add_all(
        [
            AIModelPriceComponent(
                ai_model_id=model.id,
                component_type="input",
                price_per_unit_usd=Decimal(str(cost_in * 1000)),
                effective_from=effective_from,
            ),
            AIModelPriceComponent(
                ai_model_id=model.id,
                component_type="output",
                price_per_unit_usd=Decimal(str(cost_out * 1000)),
                effective_from=effective_from,
            ),
        ]
    )
    db_session.commit()


# --- Aggregation correctness ------------------------------------------------------------------


def test_summary_counts_cost_and_latency_match_fixture(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)

    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), latency_ms=1000, input_tokens=1000, output_tokens=500,
    )
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), latency_ms=2000, input_tokens=2000, output_tokens=1000,
    )
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), status="error", latency_ms=500, error_message="boom",
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs_count"] == 3
    assert body["success_count"] == 2
    assert body["error_count"] == 1
    assert body["success_rate_pct"] == round(100 * 2 / 3, 1)
    # run1: 1000/1000*0.001 + 500/1000*0.002 = 0.002 ; run2: 2000/1000*0.001 + 1000/1000*0.002 = 0.004
    assert body["total_cost_usd"] == pytest.approx(0.006, abs=1e-9)
    # avg latency spans ALL finished runs, including the error one (1000+2000+500)/3
    assert body["avg_latency_ms"] == round((1000 + 2000 + 500) / 3, 1)


def test_summary_cost_is_none_without_a_model_price(authed_client: TestClient, db_session: Session, seed: dict):
    # seed's models never have a price set — a run against one has no computable cost.
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}")
    body = resp.json()
    assert body["runs_count"] == 1  # real data either side of the missing price — not a blanket failure
    assert body["total_cost_usd"] is None


# --- Citation verification cost (docs/TASKS_CITATION_VERIFICATION.md T13) ----------------------


def _add_citation_verification(db_session: Session, run: Run, *, cost_usd: float, check_type: str = "llm") -> CitationVerification:
    """A minimal Citation + CitationVerification pair on `run`'s own RawResponse, for the cost

    aggregation tests below — the verdict/claim fields don't matter to any of these, only that
    `cost_usd`/`check_type`/`created_at` are set the way `client_month_to_date_spend`/`ops_summary`
    read them.
    """
    raw_response = db_session.query(RawResponse).filter_by(run_id=run.id).one()
    citation = Citation(raw_response_id=raw_response.id, source_url="https://example.com/a", source_domain="example.com", citation_position=0)
    db_session.add(citation)
    db_session.flush()
    verification = CitationVerification(
        citation_id=citation.id, check_type=check_type, verdict="llm_supported", cost_usd=Decimal(str(cost_usd)),
        verifier_version="1.0", created_at=run.started_at,
    )
    db_session.add(verification)
    db_session.commit()
    return verification


def test_summary_includes_verification_cost_as_its_own_field(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run = _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), input_tokens=1000, output_tokens=500,
    )
    _add_citation_verification(db_session, run, cost_usd=0.0086)

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}")
    body = resp.json()
    # run cost (0.001+0.001) unaffected by the verification cost — the two are separate fields.
    assert body["total_cost_usd"] == pytest.approx(0.002, abs=1e-9)
    assert body["total_verification_cost_usd"] == pytest.approx(0.0086, abs=1e-9)


def test_summary_verification_cost_is_none_with_no_citation_verifications(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}")
    assert resp.json()["total_verification_cost_usd"] is None


def test_client_month_to_date_spend_includes_citation_verification_cost(db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    month_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    run = _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=month_start + timedelta(days=2), input_tokens=1000, output_tokens=500,
    )
    _add_citation_verification(db_session, run, cost_usd=0.05)

    spend = client_month_to_date_spend(db_session, client_id=client_row.id, month_start=month_start)

    assert spend == pytest.approx(0.002 + 0.05, abs=1e-9)


def test_client_month_to_date_spend_treats_a_missing_side_as_zero_not_none(db_session: Session, seed: dict):
    """A client with real run cost but no citation verification yet must still get a real total,

    not `None` — only BOTH sides being unknown should produce `None` (design decision 26).
    """
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    month_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=month_start + timedelta(days=2), input_tokens=1000, output_tokens=500,
    )

    spend = client_month_to_date_spend(db_session, client_id=client_row.id, month_start=month_start)

    assert spend == pytest.approx(0.002, abs=1e-9)


def test_client_month_to_date_spend_is_none_with_nothing_at_all(db_session: Session, seed: dict):
    client_row, _ = _client_with_prompt_set(db_session, "Acme", "acme")

    spend = client_month_to_date_spend(db_session, client_id=client_row.id, month_start=datetime(2026, 9, 1, tzinfo=timezone.utc))

    assert spend is None


def test_average_llm_judge_cost_per_citation_averages_past_llm_verifications(db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run1 = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))
    run2 = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=2))
    _add_citation_verification(db_session, run1, cost_usd=0.01)
    _add_citation_verification(db_session, run2, cost_usd=0.03)
    # A quote-check verification must not count toward the LLM-judge average.
    _add_citation_verification(db_session, run1, cost_usd=100.0, check_type="quote")

    average = average_llm_judge_cost_per_citation(db_session)

    assert average == pytest.approx((0.01 + 0.03) / 2, abs=1e-9)


def test_average_llm_judge_cost_per_citation_is_none_without_any_history(db_session: Session, seed: dict):
    assert average_llm_judge_cost_per_citation(db_session) is None


def test_daily_fills_gap_days_and_matches_range_granularity(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)

    day_a = (now - timedelta(days=5)).date()
    day_b = (now - timedelta(days=2)).date()
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=5))
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=2), status="error", error_message="boom")

    resp = authed_client.get(f"/ops/api/daily?range=7d&client_id={client_row.id}")
    body = resp.json()
    assert body["granularity"] == "day"
    points_by_date = {p["bucket_start"]: p for p in body["points"]}
    assert points_by_date[day_a.isoformat()]["success_count"] == 1
    assert points_by_date[day_a.isoformat()]["error_count"] == 0
    assert points_by_date[day_b.isoformat()]["success_count"] == 0
    assert points_by_date[day_b.isoformat()]["error_count"] == 1
    # a day strictly between the two with no runs at all is still present, zero-filled
    gap_day = (now - timedelta(days=3)).date().isoformat()
    assert gap_day in points_by_date
    assert points_by_date[gap_day] == {"bucket_start": gap_day, "success_count": 0, "error_count": 0}


@pytest.mark.parametrize("range_, expected_granularity", [("90d", "week"), ("quarter", "week"), ("all", "month")])
def test_daily_granularity_follows_range(authed_client: TestClient, db_session: Session, seed: dict, range_, expected_granularity):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=datetime.now(timezone.utc) - timedelta(days=1))

    resp = authed_client.get(f"/ops/api/daily?range={range_}&client_id={client_row.id}")
    assert resp.json()["granularity"] == expected_granularity


def test_provider_cost_rows_ranked_by_total_cost(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.001)  # cheap
    _price(db_session, seed["anthropic_model"], 0.01, 0.01)  # expensive
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))
    _make_run(db_session, prompt, model_id=seed["anthropic_model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    rows = authed_client.get(f"/ops/api/providers?range=30d&client_id={client_row.id}").json()
    assert [r["provider_name"] for r in rows] == ["Anthropic Claude", "Google Gemini"]  # pricier first
    assert rows[0]["total_cost_usd"] > rows[1]["total_cost_usd"]


def test_prompt_lineage_aggregation_agrees_across_endpoints(authed_client: TestClient, db_session: Session, seed: dict):
    """Regression guard: /api/prompts, /api/summary?prompt_id=, and /api/prompt-detail must all
    report the SAME lineage-wide total for a prompt with edited history — a prior version of
    /api/prompt-detail built its own separate lineage query that silently disagreed with
    /api/summary's (found by exercising the T3 UI against real data)."""
    _price(db_session, seed["model"], 0.001, 0.001)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    old_version = _make_prompt(db_session, prompt_set, seed["market"].id, "Old wording?", is_current_version=False)
    current_version = _make_prompt(
        db_session, prompt_set, seed["market"].id, "New wording?", root_prompt_id=old_version.id, version=2
    )
    now = datetime.now(timezone.utc)
    _make_run(db_session, old_version, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=5))
    _make_run(db_session, current_version, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    prompts_list = authed_client.get(f"/ops/api/prompts?range=30d&prompt_set_id={prompt_set.id}").json()
    assert len(prompts_list) == 1
    assert prompts_list[0]["prompt_id"] == current_version.id
    assert prompts_list[0]["runs_count"] == 2

    summary_by_current = authed_client.get(f"/ops/api/summary?range=30d&prompt_id={current_version.id}").json()
    summary_by_old = authed_client.get(f"/ops/api/summary?range=30d&prompt_id={old_version.id}").json()
    detail = authed_client.get(f"/ops/api/prompt-detail?range=30d&prompt_id={current_version.id}").json()

    assert summary_by_current["runs_count"] == 2
    assert summary_by_old["runs_count"] == 2  # querying either version's id returns the whole lineage
    assert len(detail["recent_runs"]) == 2
    assert detail["prompt_text"] == "New wording?"  # always the current version's own text/id, not the lineage root's


def test_prompt_detail_model_comparison_and_recent_runs(authed_client: TestClient, db_session: Session, seed: dict, admin_user: User):
    _price(db_session, seed["model"], 0.001, 0.001)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)

    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(hours=1), triggered_by_user_id=admin_user.id,
    )
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(hours=2), status="error", error_message="rate limited",
    )

    detail = authed_client.get(f"/ops/api/prompt-detail?range=30d&prompt_id={prompt.id}").json()
    assert detail["models"][0]["model_name"] == seed["model"].model_name
    assert detail["models"][0]["runs_count"] == 2
    assert detail["models"][0]["success_rate_pct"] == 50.0
    assert detail["runs_url"] == f"/prompts/{prompt.id}?scope=lineage"

    runs_by_status = {r["status"]: r for r in detail["recent_runs"]}
    assert runs_by_status["success"]["triggered_by_user_name"] == admin_user.name
    assert runs_by_status["success"]["is_scheduler"] is False
    assert runs_by_status["error"]["error_message"] == "rate limited"
    assert runs_by_status["error"]["cost_usd"] is None
    # most recent first
    assert detail["recent_runs"][0]["run_id"] != detail["recent_runs"][1]["run_id"]
    assert detail["recent_runs"][0]["started_at"] > detail["recent_runs"][1]["started_at"]


# --- Role gate ---------------------------------------------------------------------------------


def test_viewer_gets_403_on_ops_page_and_api(viewer_client: TestClient):
    assert viewer_client.get("/ops").status_code == 403
    assert viewer_client.get("/ops/api/summary").status_code == 403
    assert viewer_client.get("/ops/api/clients").status_code == 403
    assert viewer_client.get("/ops/api/users").status_code == 403


def test_unauthenticated_gets_401_on_ops_api(client: TestClient):
    resp = client.get("/ops/api/summary")
    assert resp.status_code == 401


def test_editor_is_allowed_admin_is_allowed(authed_client: TestClient, admin_client: TestClient):
    # authed_client logs in as the editor fixture (tests/conftest.py) — admin/editor both pass.
    assert authed_client.get("/ops/api/summary").status_code == 200
    assert admin_client.get("/ops/api/summary").status_code == 200


# --- Cross-client / cross-user isolation --------------------------------------------------------


def test_ops_never_leaks_another_clients_data(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.001)
    acme, acme_set = _client_with_prompt_set(db_session, "Acme", "acme")
    globex, globex_set = _client_with_prompt_set(db_session, "Globex", "globex")
    acme_prompt = _make_prompt(db_session, acme_set, seed["market"].id, "Acme prompt?")
    globex_prompt = _make_prompt(db_session, globex_set, seed["market"].id, "Globex prompt?")
    now = datetime.now(timezone.utc)

    _make_run(db_session, acme_prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))
    _make_run(db_session, globex_prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))
    _make_run(db_session, globex_prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1), status="error")

    acme_summary = authed_client.get(f"/ops/api/summary?range=30d&client_id={acme.id}").json()
    globex_summary = authed_client.get(f"/ops/api/summary?range=30d&client_id={globex.id}").json()
    assert acme_summary["runs_count"] == 1
    assert acme_summary["error_count"] == 0
    assert globex_summary["runs_count"] == 2
    assert globex_summary["error_count"] == 1

    all_clients = authed_client.get("/ops/api/clients?range=30d").json()
    by_name = {row["client_name"]: row for row in all_clients}
    assert by_name["Acme"]["runs_count"] == 1
    assert by_name["Globex"]["runs_count"] == 2

    acme_sets = authed_client.get(f"/ops/api/prompt-sets?range=30d&client_id={acme.id}").json()
    assert [s["name"] for s in acme_sets] == ["Acme prompts"]  # Globex's set never appears here


def test_user_scoped_endpoints_isolate_across_users(
    authed_client: TestClient, db_session: Session, seed: dict, admin_user: User, editor_user: User
):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), triggered_by_user_id=admin_user.id)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), triggered_by_user_id=editor_user.id)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), triggered_by_user_id=editor_user.id, status="error")

    admin_summary = authed_client.get(f"/ops/api/summary?range=30d&user_id={admin_user.id}").json()
    editor_summary = authed_client.get(f"/ops/api/summary?range=30d&user_id={editor_user.id}").json()
    assert admin_summary["runs_count"] == 1
    assert editor_summary["runs_count"] == 2

    editor_detail = authed_client.get(f"/ops/api/user-detail?range=30d&user_id={editor_user.id}").json()
    assert editor_detail["user_name"] == editor_user.name
    assert sum(c["runs_count"] for c in editor_detail["by_client"]) == 2
    assert len(editor_detail["recent_runs"]) == 2


def test_scheduler_and_unknown_attribution_rows_are_distinguished(
    authed_client: TestClient, db_session: Session, seed: dict, admin_user: User
):
    """The design-decision-10 critical case, extended with the unknown-attribution find from T2:
    a real user, the Scheduler pseudo-user, and pre-attribution-tracking runs must end up as three
    separate rows, never folded into one NULL-user bucket."""
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)

    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), trigger_type="manual", triggered_by_user_id=admin_user.id)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), trigger_type="scheduled", triggered_by_user_id=None)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=now - timedelta(days=1), trigger_type="manual", triggered_by_user_id=None)

    rows = authed_client.get(f"/ops/api/users?range=30d&client_id={client_row.id}").json()
    assert len(rows) == 3
    scheduler_rows = [r for r in rows if r["is_scheduler"]]
    unknown_rows = [r for r in rows if r["is_unknown_attribution"]]
    real_rows = [r for r in rows if not r["is_scheduler"] and not r["is_unknown_attribution"]]
    assert len(scheduler_rows) == 1
    assert len(unknown_rows) == 1
    assert len(real_rows) == 1
    assert real_rows[0]["user_id"] == admin_user.id
    assert scheduler_rows[0]["user_id"] is None
    assert unknown_rows[0]["user_id"] is None
    assert all(r["runs_count"] == 1 for r in rows)

    scheduler_detail = authed_client.get("/ops/api/user-detail?range=30d&user_id=scheduler").json()
    assert scheduler_detail["is_scheduler"] is True
    # the scheduler detail must never pick up the unknown-attribution run
    assert sum(c["runs_count"] for c in scheduler_detail["by_client"]) == 1


def test_gemini_shaped_token_usage_is_aggregated_correctly(authed_client: TestClient, db_session: Session, seed: dict):
    """run_cost_sql_expr's SQL-side COALESCE across provider key shapes, exercised at the
    aggregation level (cost.py's own unit tests only cover estimate_run_cost in Python)."""
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1),
        input_tokens=57, output_tokens=1061, token_usage_shape="gemini",
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()
    assert resp["total_cost_usd"] == pytest.approx(57 / 1000 * 0.001 + 1061 / 1000 * 0.002, abs=1e-9)


# --- CC-7: price versioning, Python/SQL parity, token aggregation --------------------------------


def _price_two_versions(
    db_session: Session, model_id: int, *, old_input: str, old_output: str, old_from, new_input: str, new_output: str, new_from
) -> None:
    """Two 'input'/'output' version generations for one model, at distinct effective_from times —
    unlike `_price`, which only ever writes one (the versioning tests need a real predecessor to
    prove the resolver picks the one effective at a run's OWN time, not simply "the latest row").
    """
    db_session.add_all(
        [
            AIModelPriceComponent(ai_model_id=model_id, component_type="input", price_per_unit_usd=Decimal(old_input), effective_from=old_from),
            AIModelPriceComponent(ai_model_id=model_id, component_type="output", price_per_unit_usd=Decimal(old_output), effective_from=old_from),
            AIModelPriceComponent(ai_model_id=model_id, component_type="input", price_per_unit_usd=Decimal(new_input), effective_from=new_from),
            AIModelPriceComponent(ai_model_id=model_id, component_type="output", price_per_unit_usd=Decimal(new_output), effective_from=new_from),
        ]
    )
    db_session.commit()


def test_price_versioning_resolves_correctly_in_python(db_session: Session, seed: dict):
    model = seed["model"]
    now = datetime.now(timezone.utc)
    old_from = now - timedelta(days=2)
    new_from = now - timedelta(hours=1)
    _price_two_versions(
        db_session, model.id, old_input="1.0", old_output="5.0", old_from=old_from, new_input="2.0", new_output="10.0", new_from=new_from
    )

    components = load_price_components(db_session, [model.id])[model.id]
    old_run_time = now - timedelta(days=1, hours=1)  # after old_from, before new_from
    new_run_time = now - timedelta(minutes=10)  # after new_from

    old_prices = prices_at(components, old_run_time)
    new_prices = prices_at(components, new_run_time)
    assert old_prices == {"input": Decimal("1.000000"), "output": Decimal("5.000000")}
    assert new_prices == {"input": Decimal("2.000000"), "output": Decimal("10.000000")}

    token_usage = {"input_tokens": 1000, "output_tokens": 200}
    old_cost = estimate_run_cost(token_usage, model, old_prices)
    new_cost = estimate_run_cost(token_usage, model, new_prices)
    assert old_cost == round(1000 / 1e6 * 1.0 + 200 / 1e6 * 5.0, 6)
    assert new_cost == round(1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0, 6)
    assert new_cost > old_cost


def test_price_versioning_resolves_correctly_via_sql(authed_client: TestClient, db_session: Session, seed: dict):
    model = seed["model"]
    now = datetime.now(timezone.utc)
    old_from = now - timedelta(days=2)
    new_from = now - timedelta(hours=1)
    _price_two_versions(
        db_session, model.id, old_input="1.0", old_output="5.0", old_from=old_from, new_input="2.0", new_output="10.0", new_from=new_from
    )
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")

    old_run_time = now - timedelta(days=1, hours=1)
    new_run_time = now - timedelta(minutes=10)
    _make_run(db_session, prompt, model_id=model.id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=old_run_time, input_tokens=1000, output_tokens=200)
    _make_run(db_session, prompt, model_id=model.id, market_id=seed["market"].id, persona_id=seed["persona"].id,
              started_at=new_run_time, input_tokens=1000, output_tokens=200)

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()

    # If the SQL twin used "today's" price for both runs instead of each run's own effective
    # price, this would come out as 2x the new-price cost instead — a clearly different number.
    expected = (1000 / 1e6 * 1.0 + 200 / 1e6 * 5.0) + (1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0)
    assert resp["total_cost_usd"] == pytest.approx(expected, abs=1e-9)


def test_python_and_sql_cost_totals_agree_across_mixed_provider_shapes(authed_client: TestClient, db_session: Session, seed: dict):
    """The only guard against `run_cost_sql_expr` and `estimate_run_cost` silently drifting apart
    over time (docs/TASKS_COST_COMPONENTS.md CC-7 step 3) — every other test here exercises one or
    the other, never both against the exact same data.
    """
    effective_from = datetime.now(timezone.utc) - timedelta(days=365)
    db_session.add_all(
        [
            # Anthropic: input/output + cache_read + both write tiers.
            AIModelPriceComponent(ai_model_id=seed["anthropic_model"].id, component_type="input", price_per_unit_usd=Decimal("2.0"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["anthropic_model"].id, component_type="output", price_per_unit_usd=Decimal("10.0"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["anthropic_model"].id, component_type="cache_read", price_per_unit_usd=Decimal("0.20"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["anthropic_model"].id, component_type="cache_write_5m", price_per_unit_usd=Decimal("2.50"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["anthropic_model"].id, component_type="cache_write_1h", price_per_unit_usd=Decimal("4.0"), effective_from=effective_from),
            # Gemini: input/output + cache_read.
            AIModelPriceComponent(ai_model_id=seed["model"].id, component_type="input", price_per_unit_usd=Decimal("0.25"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["model"].id, component_type="output", price_per_unit_usd=Decimal("1.50"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["model"].id, component_type="cache_read", price_per_unit_usd=Decimal("0.025"), effective_from=effective_from),
            # OpenAI: input/output + cache_read + cache_write.
            AIModelPriceComponent(ai_model_id=seed["openai_model"].id, component_type="input", price_per_unit_usd=Decimal("0.20"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["openai_model"].id, component_type="output", price_per_unit_usd=Decimal("1.20"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["openai_model"].id, component_type="cache_read", price_per_unit_usd=Decimal("0.02"), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=seed["openai_model"].id, component_type="cache_write", price_per_unit_usd=Decimal("0.25"), effective_from=effective_from),
        ]
    )
    db_session.commit()

    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    started_at = datetime.now(timezone.utc) - timedelta(days=1)

    runs = []
    for model_id, token_usage in (
        (
            seed["anthropic_model"].id,
            {
                "input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500,
                "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 100},
            },
        ),
        (seed["model"].id, {"prompt_token_count": 2000, "candidates_token_count": 300, "cached_content_token_count": 800}),
        (
            seed["openai_model"].id,
            {"input_tokens": 10_000, "output_tokens": 1_000, "input_tokens_details": {"cached_tokens": 4_000, "cache_write_tokens": 1_000}},
        ),
        (seed["model"].id, {"prompt_token_count": 500, "candidates_token_count": 50}),  # no cache activity
    ):
        run = Run(prompt_id=prompt.id, model_id=model_id, market_id=seed["market"].id, persona_id=seed["persona"].id, status="success", started_at=started_at)
        db_session.add(run)
        db_session.flush()
        db_session.add(RawResponse(run_id=run.id, raw_payload={"answer": "..."}, token_usage=token_usage))
        runs.append(run)
    db_session.commit()

    model_ids = list({r.model_id for r in runs})
    components_by_model = load_price_components(db_session, model_ids)
    models_by_id = {m.id: m for m in db_session.query(AIModel).filter(AIModel.id.in_(model_ids)).all()}
    python_total = 0.0
    for run in runs:
        raw_response = db_session.query(RawResponse).filter_by(run_id=run.id).one()
        model = models_by_id[run.model_id]
        prices = prices_at(components_by_model.get(model.id, []), run.started_at)
        cost = estimate_run_cost(raw_response.token_usage, model, prices)
        assert cost is not None
        python_total += cost

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()
    assert resp["runs_count"] == 4
    assert resp["total_cost_usd"] == pytest.approx(python_total, abs=1e-6)


def test_token_totals_match_seeded_data(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    started_at = datetime.now(timezone.utc) - timedelta(days=1)

    run1 = Run(prompt_id=prompt.id, model_id=seed["anthropic_model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, status="success", started_at=started_at)
    db_session.add(run1)
    db_session.flush()
    db_session.add(RawResponse(run_id=run1.id, raw_payload={"answer": "..."}, token_usage={
        "input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500,
        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 100},
    }))
    run2 = Run(prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, status="success", started_at=started_at)
    db_session.add(run2)
    db_session.flush()
    db_session.add(RawResponse(run_id=run2.id, raw_payload={"answer": "..."}, token_usage={
        "prompt_token_count": 2000, "candidates_token_count": 300, "cached_content_token_count": 800,
    }))
    db_session.commit()

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()

    assert resp["total_input_tokens"] == 1000 + 2000  # RAW counts, never netted against cache
    assert resp["total_output_tokens"] == 200 + 300
    assert resp["total_cache_read_tokens"] == 500 + 800
    assert resp["total_cache_write_tokens"] == 300 + 100  # both Anthropic write tiers summed


def test_token_totals_are_none_when_no_run_in_scope_has_a_raw_responses_row(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1), status="error", error_message="boom",
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()

    assert resp["runs_count"] == 1
    assert resp["total_input_tokens"] is None
    assert resp["total_output_tokens"] is None
    assert resp["total_cache_read_tokens"] is None
    assert resp["total_cache_write_tokens"] is None


def test_token_totals_are_none_for_a_run_with_an_unrecognized_token_usage_shape(authed_client: TestClient, db_session: Session, seed: dict):
    """Regression guard (code review finding): a RawResponse row that exists but whose
    token_usage matches no known provider shape must make total_input_tokens/total_output_tokens
    None, the same as "no RawResponse at all" — never a silent 0 that dilutes/hides an unknown run
    (design decision 7's "no data is masked as zero" discipline, extended from cost to tokens).
    """
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    run = Run(
        prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        status="success", started_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(RawResponse(run_id=run.id, raw_payload={"answer": "..."}, token_usage={"some_other_provider_field": 123}))
    db_session.commit()

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()

    assert resp["runs_count"] == 1
    assert resp["total_input_tokens"] is None
    assert resp["total_output_tokens"] is None


def test_cache_token_totals_are_zero_not_none_when_a_run_has_no_cache_activity(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1), input_tokens=500, output_tokens=50,
    )

    resp = authed_client.get(f"/ops/api/summary?range=30d&client_id={client_row.id}").json()

    assert resp["total_input_tokens"] == 500
    assert resp["total_output_tokens"] == 50
    assert resp["total_cache_read_tokens"] == 0  # a real run exists, so this is a known zero, not unknown
    assert resp["total_cache_write_tokens"] == 0


def test_provider_rows_include_token_totals(authed_client: TestClient, db_session: Session, seed: dict):
    _price(db_session, seed["model"], 0.001, 0.002)
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=1), input_tokens=57, output_tokens=1061, token_usage_shape="gemini",
    )

    rows = authed_client.get(f"/ops/api/providers?range=30d&client_id={client_row.id}").json()

    assert len(rows) == 1
    assert rows[0]["total_input_tokens"] == 57
    assert rows[0]["total_output_tokens"] == 1061


# --- PRE-1: test clients are excluded from the ops aggregates ------------------------------------


@pytest.fixture
def two_clients_one_flagged(db_session: Session, seed: dict) -> dict:
    """One ordinary client and one flagged `is_test`, each with a successful run on the same model.

    Priced, so the exclusion can be checked on `total_cost_usd` too — the figure the whole point of
    the flag hangs on (docs/TASKS_PRE_SCHEDULER.md PRE-1) — not just on run counts.
    """
    _price(db_session, seed["model"], 0.001, 0.002)
    real_client, real_set = _client_with_prompt_set(db_session, "Acme", "acme")
    test_client, test_set = _client_with_prompt_set(db_session, "Test Acme", "test-acme")
    test_client.is_test = True
    db_session.commit()

    now = datetime.now(timezone.utc)
    common = dict(
        model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), input_tokens=1000, output_tokens=500,
    )
    real_prompt = _make_prompt(db_session, real_set, seed["market"].id, "Real prompt?")
    test_prompt = _make_prompt(db_session, test_set, seed["market"].id, "Test prompt?")
    _make_run(db_session, real_prompt, **common)
    _make_run(db_session, test_prompt, **common)
    return {
        "real_client": real_client, "test_client": test_client,
        "real_prompt": real_prompt, "test_prompt": test_prompt,
        "real_set": real_set, "test_set": test_set,
    }


def test_summary_excludes_test_client_by_default(authed_client: TestClient, two_clients_one_flagged: dict):
    body = authed_client.get("/ops/api/summary?range=30d").json()
    assert body["runs_count"] == 1
    assert body["total_cost_usd"] == pytest.approx(0.002, abs=1e-9)


def test_summary_includes_test_client_on_request(authed_client: TestClient, two_clients_one_flagged: dict):
    body = authed_client.get("/ops/api/summary?range=30d&include_test=1").json()
    assert body["runs_count"] == 2
    assert body["total_cost_usd"] == pytest.approx(0.004, abs=1e-9)


def test_excluded_and_included_run_counts_reconcile(authed_client: TestClient, two_clients_one_flagged: dict):
    """The two views must account for every run between them — no run silently dropped by both."""
    without = authed_client.get("/ops/api/summary?range=30d").json()["runs_count"]
    with_test = authed_client.get("/ops/api/summary?range=30d&include_test=1").json()["runs_count"]
    assert without == 1 and with_test == 2
    assert with_test - without == 1  # exactly the flagged client's runs, nothing else moved


def test_client_axis_omits_test_client_by_default(authed_client: TestClient, two_clients_one_flagged: dict):
    names = [r["client_name"] for r in authed_client.get("/ops/api/clients?range=30d").json()]
    assert names == ["Acme"]

    names_with_test = [r["client_name"] for r in authed_client.get("/ops/api/clients?range=30d&include_test=1").json()]
    assert sorted(names_with_test) == ["Acme", "Test Acme"]


def test_daily_providers_and_users_axes_all_honour_the_flag(authed_client: TestClient, two_clients_one_flagged: dict):
    """Every remaining global-view axis, not just the KPI tiles — a filter that only reaches some of
    the page is worse than none at all (PRE-1 step 5).
    """
    daily = authed_client.get("/ops/api/daily?range=30d").json()
    assert sum(p["success_count"] for p in daily["points"]) == 1

    providers = authed_client.get("/ops/api/providers?range=30d").json()
    assert sum(r["runs_count"] for r in providers) == 1

    users = authed_client.get("/ops/api/users?range=30d").json()
    assert sum(r["runs_count"] for r in users) == 1

    daily_with = authed_client.get("/ops/api/daily?range=30d&include_test=1").json()
    assert sum(p["success_count"] for p in daily_with["points"]) == 2
    assert sum(r["runs_count"] for r in authed_client.get("/ops/api/providers?range=30d&include_test=1").json()) == 2
    assert sum(r["runs_count"] for r in authed_client.get("/ops/api/users?range=30d&include_test=1").json()) == 2


def test_drilldown_endpoints_that_rebuild_their_own_query_honour_the_flag(
    authed_client: TestClient, two_clients_one_flagged: dict
):
    """`prompt-sets`, `prompts` and `user-detail` derive a narrowed query themselves instead of
    using `scope.run_ids_query`, so they are the three that can silently ignore `include_test` —
    the exact failure PRE-1 step 5 names. Drilled into the FLAGGED client, where the default filter
    means "show nothing".
    """
    test_client = two_clients_one_flagged["test_client"]
    test_set = two_clients_one_flagged["test_set"]

    assert authed_client.get(f"/ops/api/prompt-sets?range=30d&client_id={test_client.id}").json() == []
    assert authed_client.get(f"/ops/api/prompts?range=30d&prompt_set_id={test_set.id}").json() == []

    sets_with = authed_client.get(f"/ops/api/prompt-sets?range=30d&client_id={test_client.id}&include_test=1").json()
    assert len(sets_with) == 1 and sets_with[0]["runs_count"] == 1
    prompts_with = authed_client.get(f"/ops/api/prompts?range=30d&prompt_set_id={test_set.id}&include_test=1").json()
    assert len(prompts_with) == 1 and prompts_with[0]["runs_count"] == 1


def test_user_detail_by_client_breakdown_honours_the_flag(
    authed_client: TestClient, db_session: Session, seed: dict, editor_user: User
):
    """`user-detail`'s own narrowed query, exercised through a user who triggered runs for both a
    real and a flagged client — the flagged one must drop out of `by_client` by default.
    """
    real_client, real_set = _client_with_prompt_set(db_session, "Acme", "acme")
    test_client, test_set = _client_with_prompt_set(db_session, "Test Acme", "test-acme")
    test_client.is_test = True
    db_session.commit()

    now = datetime.now(timezone.utc)
    common = dict(
        model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=now - timedelta(days=1), triggered_by_user_id=editor_user.id,
    )
    _make_run(db_session, _make_prompt(db_session, real_set, seed["market"].id, "Real?"), **common)
    _make_run(db_session, _make_prompt(db_session, test_set, seed["market"].id, "Test?"), **common)

    body = authed_client.get(f"/ops/api/user-detail?range=30d&user_id={editor_user.id}").json()
    assert [r["client_name"] for r in body["by_client"]] == ["Acme"]

    body_with = authed_client.get(f"/ops/api/user-detail?range=30d&user_id={editor_user.id}&include_test=1").json()
    assert sorted(r["client_name"] for r in body_with["by_client"]) == ["Acme", "Test Acme"]


def test_prompt_detail_honours_the_flag(authed_client: TestClient, two_clients_one_flagged: dict):
    """The innermost drill-down level, reached on the flagged client's own prompt."""
    test_prompt = two_clients_one_flagged["test_prompt"]

    body = authed_client.get(f"/ops/api/prompt-detail?range=30d&prompt_id={test_prompt.id}").json()
    assert body["recent_runs"] == [] and body["models"] == []

    body_with = authed_client.get(f"/ops/api/prompt-detail?range=30d&prompt_id={test_prompt.id}&include_test=1").json()
    assert len(body_with["recent_runs"]) == 1


# --- Capture success by reason + verification queue (docs/TASKS_CITATION_VERIFICATION.md T16) ---


def _add_citation(db_session: Session, run: Run, *, source_url: str) -> Citation:
    raw_response = db_session.query(RawResponse).filter_by(run_id=run.id).one()
    citation = Citation(raw_response_id=raw_response.id, source_url=source_url, source_domain="example.com", citation_position=0)
    db_session.add(citation)
    db_session.commit()
    db_session.refresh(citation)
    return citation


def _add_source_document(
    db_session: Session, *, url: str, fetched_at: datetime, error_reason: str | None = None, challenge_vendor: str | None = None
) -> SourceDocument:
    document = SourceDocument(requested_url=url, method="live", error_reason=error_reason, challenge_vendor=challenge_vendor, fetched_at=fetched_at, verifier_version="1.0")
    db_session.add(document)
    db_session.commit()
    return document


def test_capture_reasons_buckets_success_failure_and_not_captured(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    _add_citation(db_session, run, source_url="https://a.example/ok")
    _add_source_document(db_session, url="https://a.example/ok", fetched_at=now - timedelta(hours=1))

    _add_citation(db_session, run, source_url="https://a.example/blocked")
    _add_source_document(db_session, url="https://a.example/blocked", fetched_at=now - timedelta(hours=1), error_reason="http_403")

    _add_citation(db_session, run, source_url="https://a.example/never-captured")
    # No SourceDocument row at all for this one.

    body = authed_client.get(f"/ops/api/capture-reasons?range=30d&client_id={client_row.id}").json()
    by_reason = {(row["reason"], row["challenge_vendor"]): row["count"] for row in body}

    assert by_reason[("success", None)] == 1
    assert by_reason[("http_403", None)] == 1
    assert by_reason[("not_captured", None)] == 1


def test_capture_reasons_counts_a_url_once_regardless_of_citation_count(authed_client: TestClient, db_session: Session, seed: dict):
    """The same URL cited twice (two citations) must count once — a capture outcome is a property
    of the URL, not of how many citations happen to point at it.
    """
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    _add_citation(db_session, run, source_url="https://a.example/shared")
    _add_citation(db_session, run, source_url="https://a.example/shared")
    _add_source_document(db_session, url="https://a.example/shared", fetched_at=now - timedelta(hours=1))

    body = authed_client.get(f"/ops/api/capture-reasons?range=30d&client_id={client_row.id}").json()

    assert body == [{"reason": "success", "challenge_vendor": None, "count": 1}]


def test_capture_reasons_uses_the_latest_fetch_per_url(authed_client: TestClient, db_session: Session, seed: dict):
    """A URL captured twice (retry after a change) must be judged by its NEWEST fetch, matching
    `idx_source_documents_url_fetched` — an old failure must not outrank a later success.
    """
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=2))

    _add_citation(db_session, run, source_url="https://a.example/retried")
    _add_source_document(db_session, url="https://a.example/retried", fetched_at=now - timedelta(days=1), error_reason="timeout")
    _add_source_document(db_session, url="https://a.example/retried", fetched_at=now)

    body = authed_client.get(f"/ops/api/capture-reasons?range=30d&client_id={client_row.id}").json()

    assert body == [{"reason": "success", "challenge_vendor": None, "count": 1}]


def test_capture_reasons_carries_the_bot_challenge_vendor(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    _add_citation(db_session, run, source_url="https://a.example/cf")
    _add_source_document(db_session, url="https://a.example/cf", fetched_at=now, error_reason="bot_challenge", challenge_vendor="cloudflare")

    body = authed_client.get(f"/ops/api/capture-reasons?range=30d&client_id={client_row.id}").json()

    assert body == [{"reason": "bot_challenge", "challenge_vendor": "cloudflare", "count": 1}]


def test_verification_queue_snapshot_counts_by_kind_and_status(authed_client: TestClient, db_session: Session, seed: dict):
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    prompt = _make_prompt(db_session, prompt_set, seed["market"].id, "Test prompt?")
    now = datetime.now(timezone.utc)
    run_a = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))
    run_b = _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1))

    db_session.add_all(
        [
            VerificationJob(raw_response_id=db_session.query(RawResponse).filter_by(run_id=run_a.id).one().id, kind="capture", status="queued"),
            VerificationJob(raw_response_id=db_session.query(RawResponse).filter_by(run_id=run_a.id).one().id, kind="capture", status="queued"),
            VerificationJob(raw_response_id=db_session.query(RawResponse).filter_by(run_id=run_b.id).one().id, kind="judge", status="error"),
        ]
    )
    db_session.commit()

    body = authed_client.get("/ops/api/verification-queue?range=30d").json()
    by_bucket = {(row["kind"], row["status"]): row["count"] for row in body}

    assert by_bucket[("capture", "queued")] == 2
    assert by_bucket[("judge", "error")] == 1


def test_verification_queue_snapshot_ignores_client_and_range_filters(authed_client: TestClient, db_session: Session, seed: dict):
    """Live global state (verification_queue_snapshot's own docstring) — a job tied to a run
    outside the requested client/range still shows up, since the worker still has to process it
    regardless of what the admin currently has the page filtered to.
    """
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    other_client, other_set = _client_with_prompt_set(db_session, "Other", "other")
    prompt = _make_prompt(db_session, other_set, seed["market"].id, "Test prompt?")
    old_run = _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=datetime.now(timezone.utc) - timedelta(days=400),
    )
    db_session.add(VerificationJob(raw_response_id=db_session.query(RawResponse).filter_by(run_id=old_run.id).one().id, kind="capture", status="queued"))
    db_session.commit()

    body = authed_client.get(f"/ops/api/verification-queue?range=7d&client_id={client_row.id}").json()
    by_bucket = {(row["kind"], row["status"]): row["count"] for row in body}

    assert by_bucket.get(("capture", "queued")) == 1, "a job outside this client/range filter must still be counted"


def test_ops_page_labels_every_capture_reason(authed_client: TestClient):
    """docs/TASKS_CITATION_HARDENING.md T5 — the capture-reason label map in the page's JS is
    generated from `CAPTURE_REASONS`, so a reason added there (or to `UNVERIFIABLE_REASONS`) gets
    its translated label on the page without anyone also editing the template.
    """
    import json

    from app.i18n import get_translator
    from app.services.ops_dashboard import CAPTURE_REASONS

    t = get_translator("en")
    page = authed_client.get("/ops").text

    for reason in CAPTURE_REASONS:
        assert f"{json.dumps(reason)}: {json.dumps(t(f'ops.capture_reason_{reason}'))}" in page


def test_llm_verdicts_endpoint_counts_only_llm_verdicts_of_runs_in_scope(authed_client: TestClient, db_session: Session, seed: dict):
    """docs/TASKS_SCHEDULER_OPS.md T5 / design decision 11 — verdict ROWS (automatic and manual judging
    alike), scoped by the same run filter as the rest of the page; quote checks do not count.
    """
    client_row, prompt_set = _client_with_prompt_set(db_session, "Acme", "acme")
    other_row, other_set = _client_with_prompt_set(db_session, "Other", "other")
    now = datetime.now(timezone.utc)
    mine = _make_run(
        db_session, _make_prompt(db_session, prompt_set, seed["market"].id, "Mine?"), model_id=seed["model"].id,
        market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1),
    )
    theirs = _make_run(
        db_session, _make_prompt(db_session, other_set, seed["market"].id, "Theirs?"), model_id=seed["model"].id,
        market_id=seed["market"].id, persona_id=seed["persona"].id, started_at=now - timedelta(days=1),
    )
    _add_citation_verification(db_session, mine, cost_usd=0.01)
    _add_citation_verification(db_session, mine, cost_usd=0.01)
    _add_citation_verification(db_session, mine, cost_usd=0, check_type="quote")
    _add_citation_verification(db_session, theirs, cost_usd=0.01)

    everything = authed_client.get("/ops/api/llm-verdicts?range=30d").json()
    only_acme = authed_client.get(f"/ops/api/llm-verdicts?range=30d&client_id={client_row.id}").json()

    assert everything == {"count": 3}
    assert only_acme == {"count": 2}
