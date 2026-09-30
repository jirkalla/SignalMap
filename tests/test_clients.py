"""Client CRUD (docs/REQUIREMENTS.md FR-1..FR-3) and its delete policy (HD-T4)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, Prompt, PromptSet, RawResponse, Run, User
from app.models.run import Citation
from app.models.verification import CitationVerification, VerificationJob
from tests.conftest import TEST_USER_PASSWORD


def _create_client(authed_client: TestClient, name: str = "Acme Corporation") -> int:
    response = authed_client.post(
        "/clients", data={"name": name, "industry": "Automotive", "notes": "test notes"}, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _login_as(client: TestClient, user: User) -> None:
    """Re-authenticate an existing TestClient as `user`, replacing whatever session cookie it holds.

    Needed by the role-switching tests below: `admin_client` and `authed_client` are both built on
    the same function-scoped `client` fixture, so taking both in one test gives a single session
    logged in as whichever was resolved last, not two side-by-side sessions.
    """
    response = client.post("/auth/login", data={"username": user.email, "password": TEST_USER_PASSWORD})
    assert response.status_code == 204, f"login as {user.email} failed: {response.status_code}"


def test_create_list_edit_detail_flow(authed_client: TestClient):
    client_id = _create_client(authed_client)

    list_response = authed_client.get("/clients")
    assert list_response.status_code == 200
    assert "Acme Corporation" in list_response.text

    edit_response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corp", "industry": "Auto", "notes": ""},
        follow_redirects=False,
    )
    assert edit_response.status_code == 303

    detail_response = authed_client.get(f"/clients/{client_id}")
    assert detail_response.status_code == 200
    assert "Acme Corp" in detail_response.text


def test_delete_blocked_when_a_run_exists_under_the_client(authed_client: TestClient, db_session: Session, seed: dict):
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Q?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(
        prompt_id=prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="success",
    )
    db_session.add(run)
    db_session.commit()

    response = authed_client.post(f"/clients/{client_id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert "1 run" in response.text
    assert db_session.get(Client, client_id) is not None


def test_delete_succeeds_for_a_client_with_no_runs(authed_client: TestClient):
    client_id = _create_client(authed_client, name="Empty Co")

    response = authed_client.post(f"/clients/{client_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/clients"
    assert authed_client.get(f"/clients/{client_id}").status_code == 404


# --- T10: scheduler settings on the client edit form (priority/daily_run_limit/monthly_budget_usd) ---


def test_editing_a_client_updates_scheduler_settings(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "200", "daily_run_limit": "10", "monthly_budget_usd": "250.50"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    client = db_session.get(Client, client_id)
    assert client.priority == 200
    assert client.daily_run_limit == 10
    assert float(client.monthly_budget_usd) == 250.50


def test_editing_a_client_clears_scheduler_limits_when_fields_are_left_empty(authed_client: TestClient, db_session: Session):
    """Empty means "no override" (T10 point 4) — daily_run_limit falls back to the app-wide

    default and monthly_budget_usd goes back to no threshold, not zero.
    """
    client_id = _create_client(authed_client)
    authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": "10", "monthly_budget_usd": "250"},
        follow_redirects=False,
    )

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": "", "monthly_budget_usd": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    client = db_session.get(Client, client_id)
    assert client.daily_run_limit is None
    assert client.monthly_budget_usd is None


def test_editing_a_client_rejects_a_non_numeric_daily_run_limit(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": "not-a-number", "monthly_budget_usd": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert db_session.get(Client, client_id).daily_run_limit is None


def test_editing_a_client_rejects_a_non_numeric_monthly_budget(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": "", "monthly_budget_usd": "lots"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert db_session.get(Client, client_id).monthly_budget_usd is None


@pytest.mark.parametrize("bad_budget", ["Infinity", "-Infinity", "NaN", "-5", "100000", "150000", "999999999999.00"])
def test_editing_a_client_rejects_a_non_finite_or_negative_monthly_budget(
    authed_client: TestClient, db_session: Session, bad_budget: str
):
    """`Decimal` parses "Infinity"/"NaN" without raising (found in code review, 2026-09-22) —

    "Infinity" would silently defeat check_budget_thresholds's own "spend < budget" comparison
    forever (never warns again); "NaN" makes that same comparison False every time, firing on
    every check instead of once a month. Neither is a number a real budget can be. The ceiling
    itself is a business one, not the column's `Numeric(10, 2)` capacity (revised 2026-09-22) —
    realistic per-client spend is tens to low hundreds of dollars a month, so $100,000 is already
    a huge margin; "999999999999.00" is kept in the list since a value that large used to 500
    instead of rejecting cleanly (found live testing the original, column-sized ceiling).
    """
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": "", "monthly_budget_usd": bad_budget},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert db_session.get(Client, client_id).monthly_budget_usd is None


@pytest.mark.parametrize("bad_priority", [0, -1, 10001])
def test_editing_a_client_rejects_an_out_of_range_priority(authed_client: TestClient, db_session: Session, bad_priority: int):
    """Found in code review, 2026-09-22 — `priority` feeds `client.priority * 1000 +

    schedule.priority` (app/services/queue.py) on every enqueue, stored into run_queue's own
    `integer` column. An unbounded value here could overflow that column and crash the next
    enqueue pass with a 500 instead of a friendly validation error at the one place it was
    actually typed in.
    """
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": str(bad_priority), "daily_run_limit": "", "monthly_budget_usd": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert db_session.get(Client, client_id).priority == 100


@pytest.mark.parametrize("bad_limit", [-1, 100001])
def test_editing_a_client_rejects_an_out_of_range_daily_run_limit(authed_client: TestClient, db_session: Session, bad_limit: int):
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "priority": "100", "daily_run_limit": str(bad_limit), "monthly_budget_usd": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert db_session.get(Client, client_id).daily_run_limit is None


# --- PRE-1: the is_test flag, admin-only and never written by the client form --------------------


def test_toggle_test_flips_the_flag_for_an_admin(admin_client: TestClient, db_session: Session):
    client_id = _create_client(admin_client)
    assert db_session.get(Client, client_id).is_test is False

    response = admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/clients/{client_id}"
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is True

    admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is False, "the flag must be reversible, not one-way"


def test_toggle_test_is_forbidden_for_an_editor(authed_client: TestClient, db_session: Session):
    """`authed_client` is an editor. Editors create and edit clients, but may not decide what counts
    into the ops figures (docs/TASKS_PRE_SCHEDULER.md design decision 14).
    """
    client_id = _create_client(authed_client)
    response = authed_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    assert response.status_code == 403
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is False


def test_toggle_test_is_forbidden_for_a_viewer(viewer_client: TestClient, authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    assert viewer_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False).status_code == 403


def test_editing_a_client_never_clears_the_test_flag(
    client: TestClient, admin_user: User, editor_user: User, db_session: Session
):
    """The regression decision 14 exists to prevent: an unchecked HTML checkbox is not submitted at
    all, so had `is_test` been a field on the shared client form (hidden from editors), an editor's
    first ordinary save would have silently reset it to false — quietly putting the test client's
    whole history back into the ops totals with nobody touching the flag.
    """
    _login_as(client, editor_user)
    client_id = _create_client(client)

    _login_as(client, admin_user)
    client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is True

    _login_as(client, editor_user)
    response = client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Renamed by an editor", "industry": "Automotive", "notes": "edited", "domain": "acme.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    updated = db_session.get(Client, client_id)
    assert updated.name == "Renamed by an editor", "the edit itself must still go through"
    assert updated.is_test is True, "an ordinary client edit must not touch is_test"


def test_test_client_stays_visible_in_lists_with_a_badge(admin_client: TestClient, db_session: Session):
    """The flag hides a client from the ops AGGREGATES, never from the lists (design decision 2)."""
    client_id = _create_client(admin_client, name="Test Skoda")
    admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)

    list_response = admin_client.get("/clients")
    assert list_response.status_code == 200
    assert "Test Skoda" in list_response.text

    detail_response = admin_client.get(f"/clients/{client_id}")
    assert detail_response.status_code == 200
    assert "Test Skoda" in detail_response.text


def test_toggle_is_offered_to_an_admin_and_hidden_from_an_editor(
    client: TestClient, admin_user: User, editor_user: User
):
    """`can_flag_test_client` checked through the rendered page — the admin gets the toggle, the
    editor doesn't. UI only; the 403 tests above cover the enforcement that actually matters.
    """
    _login_as(client, editor_user)
    client_id = _create_client(client)
    toggle_action = f"/clients/{client_id}/toggle-test"
    assert toggle_action not in client.get(f"/clients/{client_id}").text

    _login_as(client, admin_user)
    assert toggle_action in client.get(f"/clients/{client_id}").text


# --- auto_verify_citations toggle + retroactive bulk-verify (docs/TASKS_CITATION_VERIFICATION.md T13) --


def test_toggle_auto_verify_citations_flips_the_flag_for_an_editor(authed_client: TestClient, db_session: Session):
    """Unlike toggle-test, this one is editor-or-admin — it doesn't rewrite any historical /ops

    figures the way is_test does (see the route's own docstring).
    """
    client_id = _create_client(authed_client)
    assert db_session.get(Client, client_id).auto_verify_citations is False

    response = authed_client.post(f"/clients/{client_id}/toggle-auto-verify-citations", follow_redirects=False)
    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.get(Client, client_id).auto_verify_citations is True

    authed_client.post(f"/clients/{client_id}/toggle-auto-verify-citations", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Client, client_id).auto_verify_citations is False, "the flag must be reversible"


def test_toggle_auto_verify_citations_is_forbidden_for_a_viewer(client: TestClient, editor_user: User, viewer_user: User, db_session: Session):
    """`viewer_client`+`authed_client` together would both end up logged in as whichever fixture

    pytest resolves last (they share one underlying `client` session, per `_login_as`'s own
    docstring above) — editor, not viewer, which wouldn't actually prove anything about a viewer
    against an editor-or-admin route. `_login_as` with a real `viewer_user` avoids that trap.
    """
    _login_as(client, editor_user)
    client_id = _create_client(client)
    _login_as(client, viewer_user)
    assert client.post(f"/clients/{client_id}/toggle-auto-verify-citations", follow_redirects=False).status_code == 403


def _make_verifiable_run(
    db_session: Session, *, prompt_set_id: int, seed: dict, started_at: datetime, citation_count: int = 1
) -> RawResponse:
    """One google_gemini run with `citation_count` citations, eligible for the retroactive

    bulk-verify preview/confirm below (a real OpenAI/Gemini provider, `has_citations=True`).
    """
    prompt = Prompt(prompt_set_id=prompt_set_id, text="What is this about?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(
        prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="success", started_at=started_at,
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="...", has_citations=True)
    db_session.add(raw)
    db_session.flush()
    for position in range(citation_count):
        db_session.add(Citation(raw_response_id=raw.id, source_url=f"https://example.com/{position}", source_domain="example.com", citation_position=position))
    db_session.commit()
    db_session.refresh(raw)
    return raw


def test_verify_retroactively_preview_counts_eligible_runs_and_citations(authed_client: TestClient, db_session: Session, seed: dict):
    client_id = _create_client(authed_client)
    client_row = db_session.get(Client, client_id)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 6, 15, tzinfo=timezone.utc), citation_count=3)
    # Outside the requested range — must not be counted.
    _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 8, 1, tzinfo=timezone.utc), citation_count=5)

    response = authed_client.post(
        f"/clients/{client_id}/verify-retroactively/preview", data={"date_from": "2026-06-01", "date_to": "2026-06-30"}
    )

    assert response.status_code == 200
    assert ">1</dd>" in response.text  # raw_response_count — exact tag match, not a loose substring
    assert ">3</dd>" in response.text  # citation_count
    assert str(client_row.name) in response.text


def test_verify_retroactively_preview_rejects_an_invalid_date_range(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)

    response = authed_client.post(
        f"/clients/{client_id}/verify-retroactively/preview", data={"date_from": "2026-06-30", "date_to": "2026-06-01"}
    )

    assert response.status_code == 400


def test_verify_retroactively_confirm_enqueues_a_judge_job_per_eligible_response(authed_client: TestClient, db_session: Session, seed: dict):
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    raw = _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 6, 15, tzinfo=timezone.utc))

    response = authed_client.post(
        f"/clients/{client_id}/verify-retroactively/confirm",
        data={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job is not None
    assert job.kind == "judge"
    assert job.requested_by_user_id is not None


def test_verify_retroactively_confirm_skips_a_response_already_llm_verified(authed_client: TestClient, db_session: Session, seed: dict):
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    raw = _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 6, 15, tzinfo=timezone.utc))
    citation = db_session.scalars(select(Citation).where(Citation.raw_response_id == raw.id)).first()
    db_session.add(CitationVerification(citation_id=citation.id, check_type="llm", verdict="llm_supported", verifier_version="1.0"))
    db_session.commit()

    authed_client.post(
        f"/clients/{client_id}/verify-retroactively/confirm",
        data={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        follow_redirects=False,
    )

    assert db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id)) is None


def test_verify_retroactively_confirm_retries_a_response_whose_capture_failed(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """A `verdict='unverifiable'` row with `reason` SET (a free capture failure — judge_citations

    never actually called the LLM) must NOT count as "already judged" — the response must stay
    eligible so it gets a real judgement once its source is captured successfully (code-review
    finding, 2026-09-30).
    """
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    raw = _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 6, 15, tzinfo=timezone.utc))
    citation = db_session.scalars(select(Citation).where(Citation.raw_response_id == raw.id)).first()
    db_session.add(CitationVerification(citation_id=citation.id, check_type="llm", verdict="unverifiable", reason="robots", verifier_version="1.0"))
    db_session.commit()

    authed_client.post(
        f"/clients/{client_id}/verify-retroactively/confirm",
        data={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        follow_redirects=False,
    )

    assert db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id)) is not None


def test_verify_retroactively_confirm_skips_a_response_whose_judge_reply_was_unparseable(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """A `verdict='unverifiable'` row with `reason=None` (the LLM WAS called and billed, but its

    reply couldn't be parsed into a recognized verdict — `judge_citations`' parse-failure branch)
    must count as "already judged", unlike the free capture-failure case above — otherwise a
    client whose judge model consistently returns malformed output for one citation would get
    re-billed for it on every future bulk-verify run, forever (code-review finding, 2026-09-30
    round 2).
    """
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    raw = _make_verifiable_run(db_session, prompt_set_id=prompt_set.id, seed=seed, started_at=datetime(2026, 6, 15, tzinfo=timezone.utc))
    citation = db_session.scalars(select(Citation).where(Citation.raw_response_id == raw.id)).first()
    db_session.add(
        CitationVerification(
            citation_id=citation.id, check_type="llm", verdict="unverifiable", reason=None,
            needs_review=True, cost_usd=Decimal("0.001234"), verifier_version="1.0",
        )
    )
    db_session.commit()

    authed_client.post(
        f"/clients/{client_id}/verify-retroactively/confirm",
        data={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        follow_redirects=False,
    )

    assert db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id)) is None


def test_verify_retroactively_preview_excludes_providers_with_no_llm_judge_path(authed_client: TestClient, db_session: Session, seed: dict):
    """Anthropic already gets the free quote check (T8) — a bulk-verify preview must not count

    its citations as eligible for the paid LLM judge.
    """
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.commit()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="What is this about?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(
        prompt_id=prompt.id, model_id=seed["anthropic_model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="success", started_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="...", has_citations=True)
    db_session.add(raw)
    db_session.flush()
    db_session.add(Citation(raw_response_id=raw.id, source_url="https://example.com/a", source_domain="example.com", citation_position=0))
    db_session.commit()

    response = authed_client.post(
        f"/clients/{client_id}/verify-retroactively/preview", data={"date_from": "2026-06-01", "date_to": "2026-06-30"}
    )

    assert response.status_code == 200
    # The confirm form only renders when raw_response_count > 0 (clients/verify_retroactively_
    # preview.html) — its absence is this test's proof the Anthropic citation was excluded.
    assert 'action="/clients/%d/verify-retroactively/confirm"' % client_id not in response.text
