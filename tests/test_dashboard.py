"""Dashboard v0 aggregation queries and JSON API (docs/TASKS_PHASE4.md P4-T5).

Fixture data is built directly against the ORM (not FakeAdapter/trigger_run) so each run's
started_at can be pinned to an exact week bucket — trigger_run always stamps now(), which can't
express "two runs in one week, a third two weeks later, with an empty week in between" needed to
exercise the league table, the own-domain matching, and the timeseries gap-filling together.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Citation, Client, Market, Persona, Prompt, PromptSet, RawResponse, Run

# Two runs in this week, a third two weeks later — 2026-01-12 (the week between them) has no
# runs at all, so it exercises the timeseries "gap weeks are 0, not missing" guarantee.
WEEK_1 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
WEEK_3 = datetime(2026, 1, 19, 12, 0, tzinfo=timezone.utc)


def _client_with_prompt(db_session: Session, seed: dict, name: str, slug: str, domain: str | None = None) -> tuple[Client, Prompt]:
    """A minimal Client -> PromptSet -> Prompt chain for one test's fixture data, independent of
    the shared `sample_prompt` fixture (which is tied to a fixed, domain-less "Test Client").
    """
    client_row = Client(name=name, slug=slug, domain=domain)
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name="Perception")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="How is this brand perceived?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return client_row, prompt


def _make_run(
    db_session: Session,
    prompt: Prompt,
    *,
    model_id: int,
    market_id: int,
    started_at: datetime,
    status: str = "success",
    citation_domains: tuple[str, ...] = (),
    analysis_skill_id: int | None = None,
    cited: bool = False,
) -> Run:
    """One Run, plus (for a successful run) its RawResponse/Citations/AnalysisResult — the direct-ORM
    equivalent of what trigger_run builds, but with a caller-chosen started_at and citation set.

    `persona_id` is looked up here (the default persona, seeded by the shared `seed` fixture)
    rather than added as a parameter every one of this helper's ~28 call sites would need to pass
    — dashboard aggregation doesn't care which persona a run used, so the default is fine.
    """
    persona_id = db_session.scalar(select(Persona.id).where(Persona.is_default.is_(True)))
    run = Run(
        prompt_id=prompt.id,
        model_id=model_id,
        market_id=market_id,
        persona_id=persona_id,
        status=status,
        started_at=started_at,
    )
    db_session.add(run)
    db_session.flush()

    if status == "success":
        raw_response = RawResponse(
            run_id=run.id,
            raw_payload={"answer": "..."},
            rendered_text="...",
            has_citations=bool(citation_domains),
        )
        db_session.add(raw_response)
        db_session.flush()
        for position, domain in enumerate(citation_domains):
            db_session.add(Citation(raw_response_id=raw_response.id, source_domain=domain, citation_position=position))
        if analysis_skill_id is not None:
            db_session.add(
                AnalysisResult(
                    raw_response_id=raw_response.id,
                    analysis_skill_id=analysis_skill_id,
                    skill_version=1,
                    output={"cited": cited, "cited_domains": list(citation_domains) if cited else []},
                )
            )

    db_session.commit()
    return run


def _add_competitive_result(
    db_session: Session, run: Run, skill_id: int, entities: list[dict], share_of_voice: float | None, position: int | None
) -> None:
    """A competitive_visibility AnalysisResult for an already-created successful `run` — separate
    from `_make_run`'s `analysis_skill_id`/`cited` params, which build a mention_visibility-shaped
    output, not this skill's entities/share_of_voice/position shape.
    """
    raw_response = db_session.scalar(select(RawResponse).where(RawResponse.run_id == run.id))
    db_session.add(
        AnalysisResult(
            raw_response_id=raw_response.id,
            analysis_skill_id=skill_id,
            skill_version=1,
            output={"entities": entities, "share_of_voice": share_of_voice, "position": position},
        )
    )
    db_session.commit()


def test_summary_counts_match_fixture(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    skill_id = seed["analysis_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("wikipedia.org", "acme.com"), analysis_skill_id=skill_id, cited=True)
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("wikipedia.org",), analysis_skill_id=skill_id, cited=False)
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_3,
              citation_domains=("news.example.com", "sub.acme.com"), analysis_skill_id=skill_id, cited=True)

    resp = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs_count"] == 3
    assert body["citations_count"] == 5
    assert body["distinct_domains_count"] == 4  # wikipedia.org, acme.com, sub.acme.com, news.example.com
    assert body["own_domain_rate"] == round(100 * 2 / 3, 1)


def test_own_domain_rate_is_none_without_a_client_domain(authed_client: TestClient, db_session: Session, seed: dict):
    no_domain_co, prompt = _client_with_prompt(db_session, seed, "No Domain Co", "no-domain-co", domain=None)
    _make_run(
        db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id,
        started_at=WEEK_1, citation_domains=("news.example.com",),
    )

    resp = authed_client.get(f"/dashboard/api/summary?client_id={no_domain_co.id}&range=all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs_count"] == 1  # real data either side of the None — proves it's not a blanket failure
    assert body["own_domain_rate"] is None


def test_domain_league_table_ranking_and_own_domain_matching(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("wikipedia.org", "acme.com"))
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("wikipedia.org",))
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_3,
              citation_domains=("news.example.com", "sub.acme.com"))

    resp = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all")
    assert resp.status_code == 200
    rows = resp.json()
    by_domain = {row["domain"]: row for row in rows}

    assert rows[0]["domain"] == "wikipedia.org"  # unambiguous top: 2 citations, everything else has 1
    assert rows[0]["rank"] == 1
    assert by_domain["wikipedia.org"]["citations_count"] == 2
    assert by_domain["wikipedia.org"]["run_coverage_pct"] == round(100 * 2 / 3, 1)
    assert by_domain["wikipedia.org"]["is_own_domain"] is False

    assert by_domain["acme.com"]["is_own_domain"] is True  # exact match
    assert by_domain["sub.acme.com"]["is_own_domain"] is True  # subdomain match (design decision 6)
    assert by_domain["news.example.com"]["is_own_domain"] is False  # not related to acme.com at all
    assert by_domain["acme.com"]["run_coverage_pct"] == round(100 * 1 / 3, 1)


def test_timeseries_fills_gap_weeks_with_zero(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    skill_id = seed["analysis_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("wikipedia.org", "acme.com"), analysis_skill_id=skill_id, cited=True)
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("wikipedia.org",), analysis_skill_id=skill_id, cited=False)
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_3,
              citation_domains=("news.example.com", "sub.acme.com"), analysis_skill_id=skill_id, cited=True)

    citations = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=citations").json()["weeks"]
    runs = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=runs").json()["weeks"]
    own_rate = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=own_rate").json()["weeks"]

    assert [w["week_start"] for w in citations] == ["2026-01-05", "2026-01-12", "2026-01-19"]
    assert [w["value"] for w in citations] == [3, 0, 2]  # gap week is 0, not missing
    assert [w["value"] for w in runs] == [2, 0, 1]
    assert [w["value"] for w in own_rate] == [50.0, 0.0, 100.0]  # week 1: 1/2 cited, week 3: 1/1 cited


def test_missing_client_id_is_a_structured_422(authed_client: TestClient):
    resp = authed_client.get("/dashboard/api/summary")
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "validation_error"


def test_unknown_client_id_is_a_structured_404(authed_client: TestClient):
    resp = authed_client.get("/dashboard/api/summary?client_id=999999")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "client_not_found"


def test_range_filter_excludes_runs_outside_the_window(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    old_run_at = datetime.now(timezone.utc) - timedelta(days=200)  # outside any rolling window, always
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=old_run_at)

    within_all = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all").json()
    within_90d = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=90d").json()

    assert within_all["runs_count"] == 1
    assert within_90d["runs_count"] == 0


def test_7d_range_excludes_runs_outside_the_window(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    outside_7d_at = datetime.now(timezone.utc) - timedelta(days=10)  # older than 7d, well within 30d
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=outside_7d_at)

    within_30d = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=30d").json()
    within_7d = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=7d").json()

    assert within_30d["runs_count"] == 1
    assert within_7d["runs_count"] == 0


def test_market_and_provider_filters_narrow_the_result(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    other_market = Market(code="de-DE", language="de", country="DE", locale_name="German (Germany)")
    db_session.add(other_market)
    db_session.commit()
    db_session.refresh(other_market)

    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=WEEK_1)
    _make_run(db_session, prompt, model_id=seed["anthropic_model"].id, market_id=other_market.id, started_at=WEEK_1)

    all_runs = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all").json()
    by_market = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all&market_id={other_market.id}").json()
    by_provider = authed_client.get(
        f"/dashboard/api/summary?client_id={acme.id}&range=all&provider_id={seed['provider'].id}"
    ).json()

    assert all_runs["runs_count"] == 2
    assert by_market["runs_count"] == 1
    assert by_provider["runs_count"] == 1


def test_only_successful_runs_are_counted(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id,
              started_at=WEEK_1, citation_domains=("wikipedia.org",))
    error_run = Run(prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id,
                     persona_id=seed["persona"].id, status="error", started_at=WEEK_1,
                     error_message="simulated provider failure")
    db_session.add(error_run)
    db_session.commit()

    resp = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all")
    assert resp.status_code == 200
    assert resp.json()["runs_count"] == 1  # the error run (no RawResponse) never counted or crashed the query


def test_dashboard_page_renders_with_client_init_data(authed_client: TestClient, db_session: Session, seed: dict):
    acme, _prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")

    resp = authed_client.get("/dashboard")
    assert resp.status_code == 200
    assert 'id="dashboard-init"' in resp.text
    assert "Acme Corp" in resp.text


def test_dashboard_never_leaks_another_clients_data(authed_client: TestClient, db_session: Session, seed: dict):
    """Regression guard for the authed_client-scoping join itself (docs/TASKS_PHASE4.md design decision
    4) — every other dashboard test only ever has one authed_client's data in the database at a time, so
    a future refactor that weakens/drops the `PromptSet.client_id == client_id` filter wouldn't be
    caught by any of them. This test puts two clients' runs/citations in the same week side by
    side and asserts summary/domains/timeseries for one never reflect the other's.
    """
    skill_id = seed["analysis_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id

    acme, acme_prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    _make_run(db_session, acme_prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("acme.com", "wikipedia.org"), analysis_skill_id=skill_id, cited=True)

    globex, globex_prompt = _client_with_prompt(db_session, seed, "Globex Inc", "globex-inc", domain="globex.com")
    _make_run(db_session, globex_prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("globex.com", "reuters.com", "reuters.com"), analysis_skill_id=skill_id, cited=True)
    _make_run(db_session, globex_prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("reuters.com",), analysis_skill_id=skill_id, cited=True)

    acme_summary = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all").json()
    globex_summary = authed_client.get(f"/dashboard/api/summary?client_id={globex.id}&range=all").json()
    assert acme_summary["runs_count"] == 1
    assert acme_summary["citations_count"] == 2
    assert globex_summary["runs_count"] == 2
    assert globex_summary["citations_count"] == 4

    acme_domains = {row["domain"] for row in authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()}
    globex_domains = {row["domain"] for row in authed_client.get(f"/dashboard/api/domains?client_id={globex.id}&range=all").json()}
    assert acme_domains == {"acme.com", "wikipedia.org"}
    assert globex_domains == {"globex.com", "reuters.com"}
    assert acme_domains.isdisjoint(globex_domains)

    acme_weeks = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=citations").json()["weeks"]
    globex_weeks = authed_client.get(f"/dashboard/api/timeseries?client_id={globex.id}&range=all&metric=citations").json()["weeks"]
    assert [w["value"] for w in acme_weeks] == [2]
    assert [w["value"] for w in globex_weeks] == [4]


# --- P5-T1: trend deltas --------------------------------------------------------------------


def test_summary_trend_deltas_match_manual_calculation(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    skill_id = seed["analysis_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id
    now = datetime.now(timezone.utc)

    # Current 30d window: 2 runs, 1 cited (50%).
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=now - timedelta(days=5),
              citation_domains=("acme.com",), analysis_skill_id=skill_id, cited=True)
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=now - timedelta(days=2),
              citation_domains=("wikipedia.org",), analysis_skill_id=skill_id, cited=False)
    # Previous 30d window (30-60 days ago): 1 run, 0 cited (0%).
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=now - timedelta(days=45),
              citation_domains=("wikipedia.org",), analysis_skill_id=skill_id, cited=False)

    resp = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=30d").json()
    assert resp["runs_count"] == 2
    assert resp["runs_count_delta_pct"] == 100.0  # 2 vs. 1 previous
    assert resp["own_domain_rate"] == 50.0
    assert resp["own_domain_rate_delta_pct"] == 50.0  # 50% - 0% = +50 points, not a relative %


def test_summary_trend_delta_is_none_without_prior_period_data(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    now = datetime.now(timezone.utc)
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=now - timedelta(days=5))

    resp = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=30d").json()
    assert resp["runs_count_delta_pct"] is None
    assert resp["citations_count_delta_pct"] is None
    assert resp["own_domain_rate_delta_pct"] is None


def test_summary_trend_delta_is_none_for_range_all(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=WEEK_1)

    resp = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all").json()
    assert resp["runs_count_delta_pct"] is None  # "all" has no natural prior period to compare against


# --- P5-T2: domain classification ------------------------------------------------------------


def test_domain_classification_is_returned_and_persists(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    _make_run(db_session, prompt, model_id=seed["model"].id, market_id=seed["market"].id, started_at=WEEK_1,
              citation_domains=("wikipedia.org",))

    before = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()
    assert before[0]["domain_type"] is None

    classify_resp = authed_client.post("/dashboard/api/domains/wikipedia.org/classify", data={"domain_type": "reference"})
    assert classify_resp.status_code == 200
    assert classify_resp.json() == {"domain": "wikipedia.org", "domain_type": "reference"}

    after = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()
    assert after[0]["domain_type"] == "reference"


def test_invalid_domain_type_is_a_structured_400(authed_client: TestClient):
    resp = authed_client.post("/dashboard/api/domains/example.com/classify", data={"domain_type": "bogus"})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "invalid_domain_type"


# --- P5-T7: competitive league table + share-of-voice/position timeseries --------------------


def test_entity_league_table_ranking_and_coverage(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    cv_skill_id = seed["competitive_visibility_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id

    run1 = _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1)
    _add_competitive_result(
        db_session, run1, cv_skill_id,
        entities=[
            {"name": "Acme Corp", "is_own_client": True, "mentioned": True, "mention_count": 3,
             "first_position": 0, "cited": False, "cited_domains": []},
            {"name": "Globex", "is_own_client": False, "mentioned": True, "mention_count": 1,
             "first_position": 50, "cited": False, "cited_domains": []},
        ],
        share_of_voice=0.75, position=1,
    )

    run2 = _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_3)
    _add_competitive_result(
        db_session, run2, cv_skill_id,
        entities=[
            {"name": "Acme Corp", "is_own_client": True, "mentioned": False, "mention_count": 0,
             "first_position": None, "cited": False, "cited_domains": []},
            {"name": "Globex", "is_own_client": False, "mentioned": True, "mention_count": 2,
             "first_position": 10, "cited": False, "cited_domains": []},
        ],
        share_of_voice=0.0, position=None,
    )

    rows = authed_client.get(f"/dashboard/api/entities?client_id={acme.id}&range=all").json()
    by_name = {row["name"]: row for row in rows}

    assert by_name["Globex"]["avg_mention_count"] == 1.5  # (1 + 2) / 2
    assert by_name["Globex"]["run_coverage_pct"] == 100.0  # mentioned in both runs
    assert by_name["Globex"]["avg_first_position"] == 30.0  # (50 + 10) / 2
    assert by_name["Globex"]["is_own_client"] is False

    assert by_name["Acme Corp"]["avg_mention_count"] == 1.5  # (3 + 0) / 2
    assert by_name["Acme Corp"]["run_coverage_pct"] == 50.0  # mentioned in only 1 of 2 runs
    assert by_name["Acme Corp"]["avg_first_position"] == 0.0  # averaged over the one mentioning run only
    assert by_name["Acme Corp"]["is_own_client"] is True


def test_entity_league_table_is_empty_without_analyzed_runs(authed_client: TestClient, db_session: Session, seed: dict):
    acme, _prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")

    rows = authed_client.get(f"/dashboard/api/entities?client_id={acme.id}&range=all").json()
    assert rows == []


def test_share_of_voice_and_position_timeseries_fill_gap_weeks_with_zero(authed_client: TestClient, db_session: Session, seed: dict):
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp", domain="acme.com")
    cv_skill_id = seed["competitive_visibility_skill"].id
    model_id, market_id = seed["model"].id, seed["market"].id

    run1 = _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1)
    _add_competitive_result(db_session, run1, cv_skill_id, entities=[], share_of_voice=0.5, position=2)

    run3 = _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_3)
    _add_competitive_result(db_session, run3, cv_skill_id, entities=[], share_of_voice=1.0, position=1)

    sov_weeks = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=share_of_voice").json()["weeks"]
    pos_weeks = authed_client.get(f"/dashboard/api/timeseries?client_id={acme.id}&range=all&metric=position").json()["weeks"]

    assert [w["week_start"] for w in sov_weeks] == ["2026-01-05", "2026-01-12", "2026-01-19"]
    assert [w["value"] for w in sov_weeks] == [50.0, 0.0, 100.0]  # gap week is 0, not missing
    assert [w["value"] for w in pos_weeks] == [2.0, 0.0, 1.0]


# --- PRE-4: cited domains are grouped by their normalized form ----------------------------------


def test_normalize_domain_sql_matches_the_python_twin(db_session: Session):
    """Table test run against real Postgres (`regexp_replace` is available there, unlike SQLite) —
    the guard against the two implementations drifting apart that docs/TASKS_PRE_SCHEDULER.md
    design decision 11 requires, since normalized_domain_sql exists only to let GROUP BY do what
    normalize_domain does for a single value elsewhere in this codebase.
    """
    from sqlalchemy import literal, select

    from app.utils import normalize_domain, normalized_domain_sql

    cases = ("www.x.com", "X.COM", "x.com", "www2.x.com", "blog.x.com", "www.meag.com", "meag.com", "")
    for raw in cases:
        sql_result = db_session.execute(select(normalized_domain_sql(literal(raw)))).scalar_one()
        assert sql_result == normalize_domain(raw), f"mismatch for {raw!r}"


def test_domain_league_merges_www_variant_citations_from_different_runs(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """'meag.com' and 'www.meag.com' cited from two SEPARATE runs must become one row with the
    combined citation count, and run_coverage_pct must stay a legitimate percentage (<= 100) —
    the two things a Python-side merge after the query cannot guarantee (docs/
    TASKS_PRE_SCHEDULER.md PRE-4).
    """
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp")
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("www.meag.com",))
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("meag.com",))

    rows = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()

    assert len(rows) == 1, "must be one merged row, not two split by www."
    assert rows[0]["domain"] == "meag.com"
    assert rows[0]["citations_count"] == 2
    assert rows[0]["run_coverage_pct"] == round(100 * 2 / 2, 1)
    assert rows[0]["run_coverage_pct"] <= 100.0


def test_a_run_citing_both_variants_is_counted_once_in_run_coverage(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """The regression grouping-in-Python would create: ONE run citing both 'meag.com' and
    'www.meag.com' must count as one run toward coverage, not two — this is exactly why the
    COUNT(DISTINCT Run.id) has to run inside the already-merged SQL group (design decision 10,
    first bullet), not be summed across two raw groups afterward.
    """
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp")
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("meag.com", "www.meag.com"))

    rows = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()

    assert len(rows) == 1
    assert rows[0]["citations_count"] == 2
    assert rows[0]["run_coverage_pct"] == 100.0, "one run in scope, cited once — never 200%"


def test_citation_totals_counts_domains_merged(authed_client: TestClient, db_session: Session, seed: dict):
    """The KPI tile (`/api/summary`'s distinct_domains_count) and the league table render on the
    same page — they must agree on how many distinct domains exist, not just on how the table
    groups its rows.
    """
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp")
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("www.meag.com", "wikipedia.org"))
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("meag.com",))

    summary = authed_client.get(f"/dashboard/api/summary?client_id={acme.id}&range=all").json()
    league = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()

    assert summary["distinct_domains_count"] == 2  # meag.com (merged) + wikipedia.org
    assert summary["distinct_domains_count"] == len(league), "the tile and the table must agree"


def test_a_domain_below_the_limit_is_promoted_once_merged(authed_client: TestClient, db_session: Session, seed: dict):
    """Two variants with 2 citations each can both sit below a domain with 3 — until merged, where
    their combined 4 belongs above it. LIMIT applied before merging can never recover this row
    (design decision 10, second bullet); LIMIT=1 here makes the failure mode unambiguous.
    """
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp")
    model_id, market_id = seed["model"].id, seed["market"].id

    # 3 citations for a domain that must NOT win once merging is correct.
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("wikipedia.org", "wikipedia.org", "wikipedia.org"))
    # 2 + 2 = 4 citations for the domain split across its www./bare variants.
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=1),
              citation_domains=("www.meag.com", "www.meag.com"))
    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1 + timedelta(hours=2),
              citation_domains=("meag.com", "meag.com"))

    rows = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all&limit=1").json()

    assert len(rows) == 1
    assert rows[0]["domain"] == "meag.com"
    assert rows[0]["citations_count"] == 4


def test_subdomains_are_not_unified_with_the_parent_domain(authed_client: TestClient, db_session: Session, seed: dict):
    """Deliberately out of scope (design decision 10): 'blog.meag.com' is a different source from
    'meag.com' and must stay its own row, even though only the leading 'www.' is stripped.
    """
    acme, prompt = _client_with_prompt(db_session, seed, "Acme Corp", "acme-corp")
    model_id, market_id = seed["model"].id, seed["market"].id

    _make_run(db_session, prompt, model_id=model_id, market_id=market_id, started_at=WEEK_1,
              citation_domains=("meag.com", "blog.meag.com"))

    rows = authed_client.get(f"/dashboard/api/domains?client_id={acme.id}&range=all").json()
    domains = {row["domain"] for row in rows}

    assert domains == {"meag.com", "blog.meag.com"}, "a subdomain is a different source, not merged"
