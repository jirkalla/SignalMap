"""Tests for app/services/queue.py and app/worker.py's process_claimed_item (docs/
TASKS_SCHEDULER.md T3) — against FakeAdapter, never a real provider.

Uses RunSchedule/RunQueueItem instances built directly against db_session, not through any
not-yet-existing CRUD routes (T5) — the same "build the row, call the pure/DB function, assert
on the row" shape as tests/test_cost.py and tests/test_scheduling_recurrence.py.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import RawResponsePayload
from app.models import AIModel, Client, Persona, Prompt, PromptSet, Run
from app.models.notification import NotificationOutbox
from app.models.schedule import RunQueueItem, RunSchedule
from app.services.queue import (
    CLIENT_PRIORITY_WEIGHT,
    cancel_queued_item,
    claim_next,
    create_retry,
    enqueue_due_schedules,
    reconcile_interrupted_runs,
    release_expired_leases,
    retry_all_errors,
)
from app.worker import process_claimed_item
from tests.conftest import TestSessionLocal
from tests.fake_adapter import FakeAdapter

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class _RetryableError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"provider error {status_code}")
        self.status_code = status_code


def _make_schedule(db_session: Session, *, prompt: Prompt, client: Client, model: AIModel, persona: Persona, admin_user, **overrides) -> RunSchedule:
    defaults = dict(
        client_id=client.id,
        target_type="prompt",
        target_id=prompt.id,
        model_ids=[model.id],
        market_id=None,
        persona_ids=[persona.id],
        frequency="daily",
        time_of_day=NOW.time(),
        timezone="Europe/Prague",
        starts_on=NOW.date() - timedelta(days=1),
        ends_on=NOW.date() + timedelta(days=30),
        priority=100,
        is_active=True,
        next_run_at=NOW - timedelta(minutes=1),
        created_by_user_id=admin_user.id,
    )
    defaults.update(overrides)
    schedule = RunSchedule(**defaults)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


def _make_queue_item(db_session: Session, *, prompt: Prompt, client: Client, model: AIModel, persona: Persona, **overrides) -> RunQueueItem:
    defaults = dict(
        schedule_id=None,
        source="schedule",
        client_id=client.id,
        prompt_id=prompt.id,
        model_id=model.id,
        market_id=prompt.market_id,
        persona_id=persona.id,
        scheduled_for=NOW,
        priority=100,
        status="queued",
    )
    defaults.update(overrides)
    item = RunQueueItem(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.fixture
def prompt_client(sample_prompt: Prompt, db_session: Session) -> Client:
    return sample_prompt.prompt_set.client


# ---------------------------------------------------------------------------
# claim_next
# ---------------------------------------------------------------------------


def test_claim_next_prefers_higher_priority(db_session, seed, sample_prompt, prompt_client):
    low = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], priority=100)
    high = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], priority=999)

    claimed = claim_next(db_session, worker_name="w1", now=NOW, lease_minutes=15)

    assert claimed.id == high.id
    db_session.refresh(low)
    assert low.status == "queued"


def test_claim_next_sets_lease_fields_and_increments_attempts(db_session, seed, sample_prompt, prompt_client):
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    claimed = claim_next(db_session, worker_name="w1", now=NOW, lease_minutes=15)

    assert claimed.status == "leased"
    assert claimed.leased_by == "w1"
    assert claimed.leased_until == NOW + timedelta(minutes=15)
    assert claimed.attempts == 1


def test_claim_next_skips_a_row_locked_by_another_session(db_session, seed, sample_prompt, prompt_client):
    """FOR UPDATE SKIP LOCKED must skip a row another session is holding, not block on it."""
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    other = TestSessionLocal()
    try:
        other.execute(select(RunQueueItem).where(RunQueueItem.id == item.id).with_for_update())

        claimed = claim_next(db_session, worker_name="w1", now=NOW, lease_minutes=15)

        assert claimed is None
    finally:
        other.rollback()
        other.close()


def test_claim_next_reclaims_a_deferred_item_once_due(db_session, seed, sample_prompt, prompt_client):
    item = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="deferred", scheduled_for=NOW - timedelta(minutes=1),
    )

    claimed = claim_next(db_session, worker_name="w1", now=NOW, lease_minutes=15)

    assert claimed.id == item.id


def test_claim_next_ignores_a_future_item(db_session, seed, sample_prompt, prompt_client):
    _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], scheduled_for=NOW + timedelta(hours=1))

    assert claim_next(db_session, worker_name="w1", now=NOW, lease_minutes=15) is None


# ---------------------------------------------------------------------------
# release_expired_leases / reconcile_interrupted_runs
# ---------------------------------------------------------------------------


def test_release_expired_leases_only_touches_items_without_a_run_id(db_session, seed, sample_prompt, prompt_client):
    in_flight_run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="scheduled", status="pending",
    )
    db_session.add(in_flight_run)
    db_session.commit()
    safe = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="leased", leased_by="dead-worker", leased_until=NOW - timedelta(minutes=1), run_id=None,
    )
    dangerous = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="leased", leased_by="dead-worker", leased_until=NOW - timedelta(minutes=1), run_id=in_flight_run.id,
    )

    released = release_expired_leases(db_session, now=NOW)

    assert released == 1
    db_session.refresh(safe)
    db_session.refresh(dangerous)
    assert safe.status == "queued"
    assert safe.leased_by is None
    assert dangerous.status == "leased"  # untouched -- reconcile_interrupted_runs owns this case


def test_reconcile_interrupted_runs_marks_stale_pending_run_and_its_queue_item(db_session, seed, sample_prompt, prompt_client):
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="scheduled", status="pending", started_at=NOW - timedelta(minutes=45),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    item = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="leased", run_id=run.id,
    )

    count = reconcile_interrupted_runs(db_session, now=NOW)

    assert count == 1
    db_session.refresh(run)
    db_session.refresh(item)
    assert run.status == "error"
    assert "worker interrupted" in run.error_message
    assert item.status == "error"
    assert "worker interrupted" in item.last_error


def test_reconcile_interrupted_runs_leaves_a_recent_pending_run_alone(db_session, seed, sample_prompt):
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="pending", started_at=NOW - timedelta(minutes=5),
    )
    db_session.add(run)
    db_session.commit()

    assert reconcile_interrupted_runs(db_session, now=NOW) == 0
    db_session.refresh(run)
    assert run.status == "pending"


# ---------------------------------------------------------------------------
# process_claimed_item
# ---------------------------------------------------------------------------


def test_grace_expired_item_is_skipped_without_running(db_session, seed, sample_prompt, prompt_client):
    item = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        scheduled_for=NOW - timedelta(hours=7),
    )

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "skipped"
    assert item.skip_reason == "grace_expired"
    assert item.run_id is None


def test_inactive_prompt_is_skipped_not_errored(db_session, seed, sample_prompt, prompt_client):
    sample_prompt.is_active = False
    db_session.commit()
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "skipped"
    assert item.skip_reason == "inactive_prompt"


def test_inactive_model_is_skipped_not_errored(db_session, seed, sample_prompt, prompt_client):
    seed["model"].is_active = False
    db_session.commit()
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "skipped"
    assert item.skip_reason == "inactive_model"


def test_collision_with_pending_run_defers_instead_of_erroring(db_session, seed, sample_prompt, prompt_client):
    pending_run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, persona_id=seed["persona"].id,
        trigger_type="manual", status="pending",
    )
    db_session.add(pending_run)
    db_session.commit()
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], attempts=1)

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "deferred"
    assert item.run_id is None
    assert item.scheduled_for == NOW + timedelta(minutes=1)


def test_dry_run_skips_without_calling_the_adapter_or_creating_a_run(db_session, seed, sample_prompt, prompt_client):
    FakeAdapter.error_to_raise = RuntimeError("must never be called in dry-run")
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    process_claimed_item(db_session, item, now=NOW, dry_run=True, grace_period_minutes=360)

    assert item.status == "skipped"
    assert item.skip_reason == "dry_run"
    assert item.run_id is None
    assert db_session.scalar(select(Run).where(Run.prompt_id == sample_prompt.id)) is None


def test_successful_execution_marks_item_done_and_writes_run_id_before_no_longer_matters(db_session, seed, sample_prompt, prompt_client):
    FakeAdapter.payload_to_return = RawResponsePayload(
        raw_payload={"answer": "ok"}, rendered_text="ok", has_citations=False, citations=[], token_usage=None
    )
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"])

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "done"
    assert item.run_id is not None
    run = db_session.get(Run, item.run_id)
    assert run.status == "success"
    assert run.trigger_type == "scheduled"
    assert run.triggered_by_user_id is None


def test_retryable_error_requeues_with_backoff_and_keeps_the_run_as_error(db_session, seed, sample_prompt, prompt_client):
    FakeAdapter.error_to_raise = _RetryableError(503)
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], attempts=1)

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "queued"
    assert item.scheduled_for == NOW + timedelta(minutes=1)
    run = db_session.get(Run, item.run_id)
    assert run.status == "error"  # execute_run always records the Run's own outcome regardless


def test_terminal_error_marks_item_error_immediately(db_session, seed, sample_prompt, prompt_client):
    FakeAdapter.error_to_raise = _RetryableError(400)  # not 429/5xx -> terminal
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], attempts=1)

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "error"
    assert item.last_error is not None
    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.run_failed")).all()
    assert len(notifications) == 1
    assert notifications[0].status == "sent"
    assert notifications[0].payload["queue_item_id"] == item.id


def test_retryable_error_does_not_notify(db_session, seed, sample_prompt, prompt_client):
    """A transport error that still has attempts left just requeues — T8's whole point is a

    notification means "this needs a human", so a 503 that might recover on attempt 2 must not
    fire schedule.run_failed yet.
    """
    FakeAdapter.error_to_raise = _RetryableError(503)
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], attempts=0)

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "queued"
    assert db_session.scalars(select(NotificationOutbox)).all() == []


def test_retryable_error_becomes_terminal_after_max_attempts(db_session, seed, sample_prompt, prompt_client):
    FakeAdapter.error_to_raise = _RetryableError(503)
    item = _make_queue_item(db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], attempts=3)

    process_claimed_item(db_session, item, now=NOW, dry_run=False, grace_period_minutes=360)

    assert item.status == "error"


# ---------------------------------------------------------------------------
# enqueue_due_schedules
# ---------------------------------------------------------------------------


def test_enqueue_creates_a_queued_item_with_client_weighted_priority(db_session, seed, sample_prompt, prompt_client, admin_user):
    prompt_client.priority = 3
    db_session.commit()
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, priority=7,
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    item = db_session.scalar(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id))
    assert item is not None
    assert item.status == "queued"
    assert item.priority == 3 * CLIENT_PRIORITY_WEIGHT + 7
    assert item.market_id == sample_prompt.market_id  # schedule.market_id was None -> prompt's own


def test_enqueue_advances_next_run_at_from_the_window_not_now(db_session, seed, sample_prompt, prompt_client, admin_user):
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, frequency="daily", next_run_at=NOW - timedelta(minutes=1),
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    db_session.refresh(schedule)
    assert schedule.next_run_at > NOW
    assert schedule.occurrences_count == 1


def test_enqueue_is_idempotent_against_a_concurrent_duplicate_insert(db_session, seed, sample_prompt, prompt_client, admin_user):
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], admin_user=admin_user,
    )
    original_window = schedule.next_run_at

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    # Simulate a second ticker pass that read the schedule before the first pass advanced it.
    db_session.refresh(schedule)
    schedule.next_run_at = original_window
    db_session.commit()
    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    items = db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all()
    assert len(items) == 1


def test_enqueue_marks_schedule_completed_when_it_has_no_further_occurrences(db_session, seed, sample_prompt, prompt_client, admin_user):
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, ends_on=None, max_occurrences=1, occurrences_count=0,
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    db_session.refresh(schedule)
    assert schedule.is_active is False
    assert schedule.inactive_reason == "completed"
    assert schedule.next_run_at is None


def test_enqueue_skips_a_window_past_grace_as_worker_down(db_session, seed, sample_prompt, prompt_client, admin_user):
    # 20h stale (beyond the 6h grace) but aligned to time_of_day so the *next* daily occurrence
    # after it lands a clean +24h later, i.e. 4h past `now` -- otherwise a time_of_day that
    # happens to fall between the stale window and `now` would squeeze in a second, legitimate
    # (in-grace) row, which isn't what this test is isolating.
    stale_window = NOW - timedelta(hours=20)
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user,
        time_of_day=stale_window.astimezone(ZoneInfo("Europe/Prague")).time(),
        next_run_at=stale_window,
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    items = db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all()
    assert len(items) == 1
    assert items[0].status == "skipped"
    assert items[0].skip_reason == "worker_down"
    db_session.refresh(schedule)
    assert schedule.occurrences_count == 1

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.window_skipped")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["count"] == 1
    assert notifications[0].payload["schedule_ids"] == [schedule.id]


def test_enqueue_window_skipped_notification_is_one_row_for_the_whole_pass(db_session, seed, sample_prompt, prompt_client, admin_user):
    """Two different schedules both missing a window in the same ticker pass must still produce

    exactly one schedule.window_skipped notification, summed across both — not one per schedule
    (design decision 25 / T8's own "souhrnně za jeden průchod tickeru" requirement).
    """
    stale_window = NOW - timedelta(hours=20)
    local_time_of_day = stale_window.astimezone(ZoneInfo("Europe/Prague")).time()
    first = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, time_of_day=local_time_of_day, next_run_at=stale_window,
    )
    second = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, time_of_day=local_time_of_day, next_run_at=stale_window,
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.window_skipped")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["count"] == 2
    assert sorted(notifications[0].payload["schedule_ids"]) == sorted([first.id, second.id])


# ---------------------------------------------------------------------------
# enqueue_due_schedules -- target_type='prompt_set' (docs/TASKS_SCHEDULER.md T5b)
# ---------------------------------------------------------------------------


def _make_prompt_set(db_session: Session, *, client: Client) -> PromptSet:
    prompt_set = PromptSet(client_id=client.id, name="Test Set")
    db_session.add(prompt_set)
    db_session.commit()
    db_session.refresh(prompt_set)
    return prompt_set


def _make_prompt(db_session: Session, *, prompt_set: PromptSet, market, text: str, is_active: bool = True) -> Prompt:
    prompt = Prompt(prompt_set_id=prompt_set.id, text=text, market_id=market.id, is_active=is_active)
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return prompt


def test_enqueue_prompt_set_fans_out_prompts_times_models_times_personas_with_shared_batch_id(
    db_session, seed, sample_prompt, prompt_client, admin_user
):
    prompt_set = _make_prompt_set(db_session, client=prompt_client)
    prompt_a = _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Prompt A")
    prompt_b = _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Prompt B")
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, target_type="prompt_set", target_id=prompt_set.id,
        model_ids=[seed["model"].id, seed["anthropic_model"].id], persona_ids=[seed["persona"].id],
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    items = db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all()
    # 2 prompts x 2 models x 1 persona = 4
    assert len(items) == 4
    assert {item.prompt_id for item in items} == {prompt_a.id, prompt_b.id}
    assert {item.model_id for item in items} == {seed["model"].id, seed["anthropic_model"].id}
    batch_ids = {item.batch_id for item in items}
    assert len(batch_ids) == 1
    assert None not in batch_ids


def test_enqueue_prompt_set_skips_inactive_prompts_without_a_row(db_session, seed, sample_prompt, prompt_client, admin_user):
    prompt_set = _make_prompt_set(db_session, client=prompt_client)
    active_prompt = _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Active", is_active=True)
    _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Inactive", is_active=False)
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, target_type="prompt_set", target_id=prompt_set.id,
        model_ids=[seed["model"].id], persona_ids=[seed["persona"].id],
    )

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    items = db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all()
    assert len(items) == 1
    assert items[0].prompt_id == active_prompt.id


def test_enqueue_prompt_set_is_idempotent_against_a_concurrent_duplicate_insert(
    db_session, seed, sample_prompt, prompt_client, admin_user
):
    prompt_set = _make_prompt_set(db_session, client=prompt_client)
    _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Prompt A")
    _make_prompt(db_session, prompt_set=prompt_set, market=seed["market"], text="Prompt B")
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        admin_user=admin_user, target_type="prompt_set", target_id=prompt_set.id,
        model_ids=[seed["model"].id], persona_ids=[seed["persona"].id],
    )
    original_window = schedule.next_run_at

    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)
    first_pass_count = len(db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all())

    # Simulate a second ticker pass that read the schedule before the first pass advanced it --
    # same scenario as the single-prompt idempotency test, but here one window is N rows, not 1.
    db_session.refresh(schedule)
    schedule.next_run_at = original_window
    db_session.commit()
    enqueue_due_schedules(db_session, now=NOW, grace_period_minutes=360)

    items = db_session.scalars(select(RunQueueItem).where(RunQueueItem.schedule_id == schedule.id)).all()
    assert first_pass_count == 2  # 2 prompts x 1 model x 1 persona
    assert len(items) == 2


# ---------------------------------------------------------------------------
# create_retry / cancel_queued_item / retry_all_errors (T7)
# ---------------------------------------------------------------------------


def test_create_retry_inserts_new_item_and_leaves_original_untouched(db_session, seed, sample_prompt, prompt_client):
    original = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="error", last_error="boom", scheduled_for=NOW - timedelta(hours=1),
    )

    retry = create_retry(db_session, original=original, now=NOW)

    assert retry.id != original.id
    assert retry.retry_of_id == original.id
    assert retry.status == "queued"
    assert retry.scheduled_for == NOW
    assert retry.batch_id is None
    assert retry.prompt_id == original.prompt_id
    assert retry.priority == original.priority

    db_session.refresh(original)
    assert original.status == "error"
    assert original.last_error == "boom"


@pytest.mark.parametrize("status", ["done", "queued", "leased", "deferred"])
def test_create_retry_rejects_non_retryable_status(db_session, seed, sample_prompt, prompt_client, status):
    original = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], status=status
    )

    with pytest.raises(ValueError):
        create_retry(db_session, original=original, now=NOW)


def test_cancel_queued_item_marks_cancelled(db_session, seed, sample_prompt, prompt_client):
    item = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"], status="queued"
    )

    cancel_queued_item(db_session, item=item, now=NOW)

    assert item.status == "cancelled"
    assert item.finished_at == NOW


def test_cancel_queued_item_rejects_leased(db_session, seed, sample_prompt, prompt_client):
    item = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="leased", leased_by="scheduler-1",
    )

    with pytest.raises(ValueError):
        cancel_queued_item(db_session, item=item, now=NOW)

    db_session.refresh(item)
    assert item.status == "leased"


def test_retry_all_errors_respects_search_filter(db_session, seed, sample_prompt, prompt_client):
    prompt_set = sample_prompt.prompt_set
    other_prompt = Prompt(prompt_set_id=prompt_set.id, text="A completely different prompt", market_id=seed["market"].id)
    db_session.add(other_prompt)
    db_session.commit()
    db_session.refresh(other_prompt)

    matching = _make_queue_item(
        db_session, prompt=sample_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="error", last_error="boom",
    )
    _make_queue_item(
        db_session, prompt=other_prompt, client=prompt_client, model=seed["model"], persona=seed["persona"],
        status="error", last_error="boom too",
    )

    retried_count = retry_all_errors(db_session, search=sample_prompt.text[:15], now=NOW)

    assert retried_count == 1
    retries = db_session.scalars(select(RunQueueItem).where(RunQueueItem.retry_of_id.is_not(None))).all()
    assert len(retries) == 1
    assert retries[0].retry_of_id == matching.id
