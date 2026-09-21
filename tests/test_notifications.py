"""Tests for app/services/notifications.py (docs/TASKS_SCHEDULER.md T8) — `notify()`, the two

periodic checks (`schedule.expiring_soon`, `budget.threshold_exceeded`), and the cooldown-guarded
`notify_worker_stale`. `schedule.run_failed`/`schedule.window_skipped` are tested in
tests/test_worker_queue.py, right alongside the code that raises them.

Same "build the row directly against db_session, call the function, assert on the outbox" shape
as the rest of this test suite — no route-level tests (the in-app dropdown has no dedicated route
test either, only browser verification).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIModel, AIModelPriceComponent, Client, Prompt, RawResponse, Run
from app.models.notification import NotificationOutbox
from app.models.schedule import RunSchedule
from app.services.notifications import check_budget_thresholds, check_expiring_schedules, notify, notify_worker_stale

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _make_schedule(db_session: Session, *, client: Client, prompt: Prompt, created_by, **overrides) -> RunSchedule:
    defaults = dict(
        client_id=client.id,
        target_type="prompt",
        target_id=prompt.id,
        model_ids=[1],
        market_id=None,
        persona_ids=[1],
        frequency="daily",
        time_of_day=NOW.time(),
        timezone="Europe/Prague",
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 1),
        occurrences_count=0,
        priority=100,
        is_active=True,
        created_by_user_id=created_by.id,
    )
    defaults.update(overrides)
    schedule = RunSchedule(**defaults)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


def _price(db_session: Session, model: AIModel, *, input_usd_per_1m: float, output_usd_per_1m: float) -> None:
    effective_from = NOW - timedelta(days=365)
    db_session.add_all(
        [
            AIModelPriceComponent(ai_model_id=model.id, component_type="input", price_per_unit_usd=Decimal(str(input_usd_per_1m)), effective_from=effective_from),
            AIModelPriceComponent(ai_model_id=model.id, component_type="output", price_per_unit_usd=Decimal(str(output_usd_per_1m)), effective_from=effective_from),
        ]
    )
    db_session.commit()


def _make_priced_run(db_session: Session, *, prompt: Prompt, model: AIModel, market_id: int, persona_id: int, started_at: datetime) -> Run:
    run = Run(
        prompt_id=prompt.id, model_id=model.id, market_id=market_id, persona_id=persona_id,
        trigger_type="scheduled", status="success", started_at=started_at,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="...", token_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000}))
    db_session.commit()
    return run


def test_notify_writes_row_and_marks_sent(db_session):
    notification = notify(db_session, "schedule.run_failed", {"queue_item_id": 1})

    assert notification.id is not None
    assert notification.status == "sent"
    assert notification.sent_at is not None
    stored = db_session.get(NotificationOutbox, notification.id)
    assert stored.event_type == "schedule.run_failed"
    assert stored.payload == {"queue_item_id": 1}


def test_check_expiring_schedules_fires_once_by_end_date(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    schedule = _make_schedule(
        db_session, client=client, prompt=sample_prompt, created_by=admin_user,
        ends_on=NOW.date() + timedelta(days=3), max_occurrences=None,
    )

    check_expiring_schedules(db_session, now=NOW)
    check_expiring_schedules(db_session, now=NOW)  # second call must not duplicate

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.expiring_soon")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["schedule_id"] == schedule.id


def test_check_expiring_schedules_fires_by_occurrences_left(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    _make_schedule(
        db_session, client=client, prompt=sample_prompt, created_by=admin_user,
        ends_on=None, max_occurrences=10, occurrences_count=8,
    )

    check_expiring_schedules(db_session, now=NOW)

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.expiring_soon")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["occurrences_left"] == 2


def test_check_expiring_schedules_ignores_schedule_far_from_end(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    _make_schedule(
        db_session, client=client, prompt=sample_prompt, created_by=admin_user,
        ends_on=NOW.date() + timedelta(days=60), max_occurrences=None,
    )

    check_expiring_schedules(db_session, now=NOW)

    assert db_session.scalars(select(NotificationOutbox)).all() == []


def test_check_budget_thresholds_fires_once_per_month(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    client.monthly_budget_usd = Decimal("1.00")
    db_session.commit()
    _price(db_session, seed["model"], input_usd_per_1m=1.0, output_usd_per_1m=1.0)
    _make_priced_run(
        db_session, prompt=sample_prompt, model=seed["model"], market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=NOW - timedelta(days=1),
    )

    check_budget_thresholds(db_session, now=NOW)
    check_budget_thresholds(db_session, now=NOW)  # second call, same month -- must not duplicate

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "budget.threshold_exceeded")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["client_id"] == client.id
    assert notifications[0].payload["spend_usd"] >= 1.0


def test_check_budget_thresholds_fires_again_next_month(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    client.monthly_budget_usd = Decimal("1.00")
    db_session.commit()
    _price(db_session, seed["model"], input_usd_per_1m=1.0, output_usd_per_1m=1.0)
    _make_priced_run(
        db_session, prompt=sample_prompt, model=seed["model"], market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=NOW - timedelta(days=1),
    )
    check_budget_thresholds(db_session, now=NOW)

    next_month = NOW + timedelta(days=31)
    _make_priced_run(
        db_session, prompt=sample_prompt, model=seed["model"], market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=next_month - timedelta(hours=1),
    )
    check_budget_thresholds(db_session, now=next_month)

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "budget.threshold_exceeded")).all()
    assert len(notifications) == 2


def test_check_budget_thresholds_ignores_client_below_budget(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    client.monthly_budget_usd = Decimal("1000.00")
    db_session.commit()
    _price(db_session, seed["model"], input_usd_per_1m=1.0, output_usd_per_1m=1.0)
    _make_priced_run(
        db_session, prompt=sample_prompt, model=seed["model"], market_id=seed["market"].id, persona_id=seed["persona"].id,
        started_at=NOW - timedelta(days=1),
    )

    check_budget_thresholds(db_session, now=NOW)

    assert db_session.scalars(select(NotificationOutbox)).all() == []


def test_notify_worker_stale_respects_cooldown(db_session):
    """Bug fix, 2026-09-21 — `_already_notified` compares against `NotificationOutbox.created_at`,

    which is stamped by the DB's own `server_default=func.now()` at insert time, not by the `now`
    this test passes in. The original version of this test relied on real wall-clock time landing
    inside the right window relative to a fixed `NOW`, which made it pass or fail depending on
    when it happened to run rather than on the behavior under test. Backdating the first
    notification's `created_at` directly makes the cooldown window deterministic regardless of
    when the suite actually runs.
    """
    notify_worker_stale(db_session, worker_name="scheduler-1", seconds_since=4000, now=NOW)
    first = db_session.scalar(select(NotificationOutbox).where(NotificationOutbox.event_type == "worker.stale"))
    first.created_at = NOW - timedelta(minutes=5)
    db_session.commit()

    notify_worker_stale(db_session, worker_name="scheduler-1", seconds_since=4010, now=NOW)

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "worker.stale")).all()
    assert len(notifications) == 1


def test_notify_worker_stale_refires_after_cooldown(db_session):
    notify_worker_stale(db_session, worker_name="scheduler-1", seconds_since=4000, now=NOW)
    first = db_session.scalar(select(NotificationOutbox).where(NotificationOutbox.event_type == "worker.stale"))
    first.created_at = NOW - timedelta(minutes=90)
    db_session.commit()

    notify_worker_stale(db_session, worker_name="scheduler-1", seconds_since=8000, now=NOW)

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "worker.stale")).all()
    assert len(notifications) == 2
