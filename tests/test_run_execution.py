"""Tests for app/services/run_execution.py's check_daily_quota (docs/TASKS_SCHEDULER.md T10,
design decision 26) — the one place both the manual trigger and the scheduler worker enforce a
client's daily run cap, so neither path can bypass the cap the other applies.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Prompt
from app.models.run import Run
from app.services.run_execution import QuotaExceededError, check_daily_quota
from tests.conftest import TestSessionLocal

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


def test_check_daily_quota_serializes_concurrent_callers_for_the_same_client(
    db_session: Session, seed: dict, sample_prompt: Prompt
):
    """Found in code review, 2026-09-22 — without the `pg_advisory_xact_lock` in

    `check_daily_quota`, two concurrent callers for the same client (a manual trigger racing the
    worker, or two manual triggers) could both count before either commits its own new Run row,
    letting the "hard cap, never bypassed" guarantee (NFR-14) slip by a run under real
    concurrency. Uses two real sessions on separate threads — not the sequential "simulate a
    second pass" trick most other tests use — because this specifically proves Postgres itself
    blocks the second caller until the first one's transaction ends, not just that the two
    checks happen to land on the right side of the limit when run one after another.
    """
    client = sample_prompt.prompt_set.client
    client.daily_run_limit = 1
    db_session.commit()

    second_session = TestSessionLocal()
    first_passed = threading.Event()
    second_returned = threading.Event()
    second_result: dict = {}

    def second_caller() -> None:
        first_passed.wait(timeout=5)
        try:
            check_daily_quota(second_session, client_id=client.id, now=NOW, default_limit=50)
            second_result["raised"] = False
        except QuotaExceededError:
            second_result["raised"] = True
        finally:
            second_session.commit()
            second_returned.set()

    thread = threading.Thread(target=second_caller)
    thread.start()
    try:
        # First caller passes (count=0 < limit=1) but deliberately doesn't commit yet, so its
        # advisory lock stays held — the second caller must block here, not race past it.
        check_daily_quota(db_session, client_id=client.id, now=NOW, default_limit=50)
        first_passed.set()
        time.sleep(0.3)
        assert not second_returned.is_set(), "second caller should still be blocked on the advisory lock"

        run = Run(
            prompt_id=sample_prompt.id,
            model_id=seed["model"].id,
            market_id=seed["market"].id,
            persona_id=seed["persona"].id,
            trigger_type="manual",
            status="success",
            started_at=NOW,
        )
        db_session.add(run)
        db_session.commit()  # releases the first caller's advisory lock

        assert second_returned.wait(timeout=5)
        assert second_result["raised"] is True
    finally:
        thread.join(timeout=5)
        second_session.close()
