"""Ops dashboard aggregation and access tests (docs/TASKS_OPS_DASHBOARD.md T4).

Fixture data is built directly against the ORM (same precedent as tests/test_dashboard.py) so each
run's started_at, trigger_type, triggered_by_user_id, and token_usage shape can be pinned exactly —
a real trigger_run/FakeAdapter flow can't express "this run was triggered by the Scheduler" or "this
run predates user-attribution tracking".

The shared `seed` fixture's models have no price set (NULL cost_per_1k_*_usd) — every cost-related
test here sets a price explicitly on the specific model it uses, rather than changing the shared
fixture (which other test files rely on staying price-less).
"""

import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Client, Prompt, PromptSet, RawResponse, Run, User


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
    model.cost_per_1k_input_usd = cost_in
    model.cost_per_1k_output_usd = cost_out
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
    assert detail["runs_url"] == f"/prompts/{prompt.id}"

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
