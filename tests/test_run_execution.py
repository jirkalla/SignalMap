"""Tests for app/services/run_execution.py's check_daily_quota (docs/TASKS_SCHEDULER.md T10,
design decision 26) — the one place both the manual trigger and the scheduler worker enforce a
client's daily run cap, so neither path can bypass the cap the other applies.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Prompt
from app.models.run import Run
from app.services.run_execution import QuotaExceededError, check_daily_quota

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _make_run(db_session: Session, *, prompt: Prompt, seed: dict, started_at: datetime) -> Run:
    run = Run(
        prompt_id=prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        trigger_type="manual",
        status="success",
        started_at=started_at,
    )
    db_session.add(run)
    db_session.commit()
    return run


def test_check_daily_quota_allows_a_client_under_its_limit(db_session: Session, seed: dict, sample_prompt: Prompt):
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 2
    db_session.commit()
    _make_run(db_session, prompt=sample_prompt, seed=seed, started_at=NOW)

    check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=50)  # 1 run < limit 2, must not raise


def test_check_daily_quota_rejects_the_run_that_would_exceed_the_limit(db_session: Session, seed: dict, sample_prompt: Prompt):
    """docs/TASKS_SCHEDULER.md T10 point 5's own scenario, at the unit level: the Nth run at a

    limit of N-1 already-run is the one that gets rejected, not silently allowed through.
    """
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 2
    db_session.commit()
    for _ in range(2):
        _make_run(db_session, prompt=sample_prompt, seed=seed, started_at=NOW)

    with pytest.raises(QuotaExceededError):
        check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=50)


def test_check_daily_quota_ignores_runs_older_than_24h(db_session: Session, seed: dict, sample_prompt: Prompt):
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 1
    db_session.commit()
    _make_run(db_session, prompt=sample_prompt, seed=seed, started_at=NOW - timedelta(hours=25))

    check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=50)  # the old run has rolled off the window


def test_check_daily_quota_falls_back_to_default_limit_when_client_has_none(db_session: Session, seed: dict, sample_prompt: Prompt):
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = None
    db_session.commit()
    _make_run(db_session, prompt=sample_prompt, seed=seed, started_at=NOW)

    with pytest.raises(QuotaExceededError):
        check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=1)


def test_check_daily_quota_counts_every_status_not_just_successes(db_session: Session, seed: dict, sample_prompt: Prompt):
    """A dispatched attempt uses up its slot in the cap whether it ended in success, error, or

    is still pending — each Run row is one real dispatched request, regardless of outcome.
    """
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 1
    db_session.commit()
    run = _make_run(db_session, prompt=sample_prompt, seed=seed, started_at=NOW)
    run.status = "error"
    db_session.commit()

    with pytest.raises(QuotaExceededError):
        check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=50)
