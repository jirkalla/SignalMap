"""Queue operations for the scheduler (docs/TASKS_SCHEDULER.md T3) — plain functions over
`run_schedules`/`run_queue`, each testable directly against a database session without the
worker process (app/worker.py) running.

Four operations, one per concern:
  - `enqueue_due_schedules` — the ticker half: turns a due `RunSchedule` into one or more
    `RunQueueItem` rows.
  - `claim_next` — the executor half: atomically takes the next item a worker should run.
  - `release_expired_leases` — returns a dead worker's unstarted claim to the queue.
  - `reconcile_interrupted_runs` — the decision-13 safety net for a claim that got as far as
    creating a `Run` before the worker died.

None of these call a provider adapter — that only happens in app/worker.py's
`process_claimed_item`, the one place `execute_run` (app/services/run_execution.py) is called
from the scheduler side.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.prompt import Prompt
from app.models.run import Run
from app.models.schedule import RunQueueItem, RunSchedule
from app.services.notifications import notify, notify_quota_exceeded
from app.services.scheduling import compute_next_run_at
from app.utils import current_prompt_version

logger = logging.getLogger(__name__)

# docs/TASKS_SCHEDULER.md design decision 20 — a semantic constant, never a runtime setting:
# changing it would redefine what every already-entered client/schedule priority number means.
CLIENT_PRIORITY_WEIGHT = 1000

# Cap on individual 'worker_down' rows a single enqueue pass writes per schedule (design
# decision 11) — a schedule that missed weeks of windows gets one summary row instead of
# hundreds of near-identical ones.
_MAX_MISSED_WINDOW_ROWS = 30


@dataclass(frozen=True)
class _EnqueueResult:
    """How many due windows of one schedule this pass skipped, broken down by reason — kept as

    two separate counts (T10) rather than one total, since `enqueue_due_schedules` rolls each up
    into its own distinct notification (`schedule.window_skipped` vs `quota.exceeded`).
    """

    worker_down_windows: int
    queue_depth_windows: int

_UNIQUE_OCCURRENCE_COLUMNS = ("schedule_id", "scheduled_for", "prompt_id", "model_id", "persona_id")


def _insert_queue_item(
    db: Session,
    *,
    schedule: RunSchedule,
    prompt_id: int,
    model_id: int,
    market_id: int,
    persona_id: int,
    scheduled_for: datetime,
    priority: int,
    status: str,
    skip_reason: str | None,
    now: datetime,
    batch_id: "uuid.UUID | None" = None,
    last_error: str | None = None,
) -> None:
    """Insert one `run_queue` row, silently doing nothing if its five-column occurrence key

    (design decisions 12 and 32) already exists — a second ticker pass racing this one, or a
    resumed pass after a crash mid-enqueue, must not double-insert the same occurrence.
    `ON CONFLICT DO NOTHING` at the database level is the only way to make that atomic; a
    SELECT-then-INSERT here would have exactly the TOCTOU race migration 0024 already had to
    close for manual triggers (app/models/run.py).
    """
    stmt = (
        pg_insert(RunQueueItem)
        .values(
            schedule_id=schedule.id,
            source="schedule",
            batch_id=batch_id,
            client_id=schedule.client_id,
            prompt_id=prompt_id,
            model_id=model_id,
            market_id=market_id,
            persona_id=persona_id,
            scheduled_for=scheduled_for,
            priority=priority,
            status=status,
            skip_reason=skip_reason,
            last_error=last_error,
            finished_at=now if status == "skipped" else None,
        )
        .on_conflict_do_nothing(index_elements=_UNIQUE_OCCURRENCE_COLUMNS)
    )
    db.execute(stmt)


def _target_prompts(db: Session, schedule: RunSchedule) -> list[Prompt]:
    """The prompt(s) one window of this schedule fans out across.

    `target_type='prompt'`: exactly the one current lineage version, regardless of its
    `is_active` — an inactive prompt still gets a queue row here, which the worker turns into a
    `skipped`/`inactive_prompt` row at claim time (design decision 18), preserving a visible
    history entry for that specific schedule's occurrence.

    `target_type='prompt_set'` (design decisions 24, 32): every *active* current-version prompt
    in the set. Deliberately filtered here, not left to the worker — a 25-prompt set is a living
    list (T5b), so a temporarily-deactivated prompt simply isn't a member of this window's fanout
    rather than generating a row that only exists to say "skipped". A prompt added to the set
    next week is picked up automatically, since this query re-reads set membership on every
    enqueue pass rather than resolving it once at schedule-creation time (same reasoning as the
    prompt-lineage resolution design decision 6 already relies on).
    """
    if schedule.target_type == "prompt":
        prompt = current_prompt_version(db, schedule.target_id)
        return [prompt] if prompt is not None else []
    if schedule.target_type == "prompt_set":
        return db.scalars(
            select(Prompt).where(
                Prompt.prompt_set_id == schedule.target_id,
                Prompt.is_current_version.is_(True),
                Prompt.is_active.is_(True),
            )
        ).all()
    raise NotImplementedError(f"schedule {schedule.id}: unknown target_type={schedule.target_type!r}")


def _enqueue_one_schedule(
    db: Session, schedule: RunSchedule, *, now: datetime, grace_period_minutes: int, max_queue_depth_per_client: int
) -> "_EnqueueResult":
    """Enqueue every due window of `schedule`. Returns the number of windows skipped as

    `worker_down` and, separately, as `queue_depth_exceeded` this pass — not the number of DB
    rows written, since windows beyond `_MAX_MISSED_WINDOW_ROWS` collapse into one summary row
    but still count as real missed windows here — so the caller can roll each count up into its
    own notification per ticker pass rather than one per schedule (design decision 25 / T8).
    """
    prompts = _target_prompts(db, schedule)
    is_batch = schedule.target_type == "prompt_set"
    fanout = [
        (prompt.id, model_id, schedule.market_id or prompt.market_id, persona_id)
        for prompt in prompts
        for model_id in schedule.model_ids
        for persona_id in schedule.persona_ids
    ]
    priority = schedule.client.priority * CLIENT_PRIORITY_WEIGHT + schedule.priority
    grace_period = timedelta(minutes=grace_period_minutes)

    # T10 point 2 — checked once here and tracked in Python rather than re-querying per window:
    # a fresh COUNT would still see this same pass's own not-yet-committed inserts (same
    # transaction), but incrementing a local counter is cheaper across a long catch-up loop and
    # gives the identical result. T5b point 5: this must gate a set-level window's fan-out
    # *before* its rows are inserted, not after — otherwise 225 rows could land and only then
    # discover they didn't fit.
    queued_depth = db.scalar(
        select(func.count(RunQueueItem.id)).where(RunQueueItem.client_id == schedule.client_id, RunQueueItem.status == "queued")
    ) or 0

    missed_window_rows_written = 0
    extra_missed_windows = 0
    last_extra_window: datetime | None = None
    queue_depth_skipped_windows = 0

    while schedule.next_run_at is not None and schedule.next_run_at <= now:
        window = schedule.next_run_at
        late_by = now - window
        # One batch_id per window (not one per ticker pass): a catch-up run that finds several
        # missed windows for the same schedule must group each window's own fanout separately,
        # so a future T6 "one row, expand to see the batch" view can tell them apart.
        window_batch_id = uuid.uuid4() if is_batch else None

        if late_by > grace_period:
            if missed_window_rows_written < _MAX_MISSED_WINDOW_ROWS:
                for prompt_id, model_id, market_id, persona_id in fanout:
                    _insert_queue_item(
                        db,
                        schedule=schedule,
                        prompt_id=prompt_id,
                        model_id=model_id,
                        market_id=market_id,
                        persona_id=persona_id,
                        scheduled_for=window,
                        priority=priority,
                        status="skipped",
                        skip_reason="worker_down",
                        now=now,
                        batch_id=window_batch_id,
                    )
                missed_window_rows_written += 1
            else:
                extra_missed_windows += 1
                last_extra_window = window
        elif fanout and queued_depth + len(fanout) > max_queue_depth_per_client:
            for prompt_id, model_id, market_id, persona_id in fanout:
                _insert_queue_item(
                    db,
                    schedule=schedule,
                    prompt_id=prompt_id,
                    model_id=model_id,
                    market_id=market_id,
                    persona_id=persona_id,
                    scheduled_for=window,
                    priority=priority,
                    status="skipped",
                    skip_reason="queue_depth_exceeded",
                    now=now,
                    batch_id=window_batch_id,
                )
            queue_depth_skipped_windows += 1
        else:
            for prompt_id, model_id, market_id, persona_id in fanout:
                _insert_queue_item(
                    db,
                    schedule=schedule,
                    prompt_id=prompt_id,
                    model_id=model_id,
                    market_id=market_id,
                    persona_id=persona_id,
                    scheduled_for=window,
                    priority=priority,
                    status="queued",
                    skip_reason=None,
                    now=now,
                    batch_id=window_batch_id,
                )
            queued_depth += len(fanout)

        # Every window consumes one occurrence, whether it ran or was skipped — otherwise a
        # worker outage or a full queue would silently extend a schedule past the lifetime its
        # max_occurrences was meant to cap (design decision 31).
        schedule.occurrences_count += 1
        schedule.next_run_at = compute_next_run_at(schedule, after=window)
        if schedule.next_run_at is None:
            schedule.is_active = False
            schedule.inactive_reason = "completed"

    if extra_missed_windows and fanout:
        prompt_id, model_id, market_id, persona_id = fanout[0]
        _insert_queue_item(
            db,
            schedule=schedule,
            prompt_id=prompt_id,
            model_id=model_id,
            market_id=market_id,
            persona_id=persona_id,
            scheduled_for=last_extra_window,
            priority=priority,
            status="skipped",
            skip_reason="worker_down",
            now=now,
            batch_id=uuid.uuid4() if is_batch else None,
            last_error=f"{extra_missed_windows} additional missed windows beyond the first "
            f"{_MAX_MISSED_WINDOW_ROWS} were also skipped (worker_down)",
        )

    schedule.last_enqueued_at = now
    return _EnqueueResult(
        worker_down_windows=missed_window_rows_written + extra_missed_windows,
        queue_depth_windows=queue_depth_skipped_windows,
    )


def enqueue_due_schedules(db: Session, *, now: datetime, grace_period_minutes: int, max_queue_depth_per_client: int) -> None:
    """The ticker: turn every active, due `RunSchedule` into `run_queue` rows.

    Runs regardless of `SCHEDULER_DRY_RUN` (design decision 15) — dry-run only short-circuits
    the executor's adapter call, never planning itself, so `/schedules` shows the same queue in
    shadow mode as it would for real.

    Fires one `schedule.window_skipped` notification for the WHOLE pass when any schedule missed
    a window as `worker_down`, not one per schedule or per window (T8) — a worker that was down
    for an hour affecting ten schedules should read as one event, not ten. Separately, fires
    `quota.exceeded` (T10) once per affected client when a window couldn't fit under
    `max_queue_depth_per_client` — `notify_quota_exceeded` itself dedupes to once/client/day, so
    several schedules for the same client hitting the cap in one pass still reads as one alert.
    """
    schedules = db.scalars(
        select(RunSchedule).where(
            RunSchedule.is_active.is_(True),
            RunSchedule.next_run_at.isnot(None),
            RunSchedule.next_run_at <= now,
        )
    ).all()
    total_skipped_windows = 0
    affected_schedule_ids: list[int] = []
    affected_client_names: list[str] = []
    queue_full_clients: dict[int, str] = {}
    for schedule in schedules:
        result = _enqueue_one_schedule(
            db, schedule, now=now, grace_period_minutes=grace_period_minutes, max_queue_depth_per_client=max_queue_depth_per_client
        )
        if result.worker_down_windows:
            total_skipped_windows += result.worker_down_windows
            affected_schedule_ids.append(schedule.id)
            if schedule.client.name not in affected_client_names:
                affected_client_names.append(schedule.client.name)
        if result.queue_depth_windows:
            queue_full_clients[schedule.client_id] = schedule.client.name
    db.commit()

    if total_skipped_windows:
        notify(
            db,
            "schedule.window_skipped",
            {"count": total_skipped_windows, "schedule_ids": affected_schedule_ids, "client_names": affected_client_names},
        )

    for client_id, client_name in queue_full_clients.items():
        notify_quota_exceeded(db, client_id=client_id, client_name=client_name, reason="queue_depth", now=now)


def claim_next(db: Session, *, worker_name: str, now: datetime, lease_minutes: int) -> RunQueueItem | None:
    """Atomically take the next item a worker should run, or `None` if nothing is due.

    `status IN ('queued', 'deferred')` — a deliberate widening of the plain `status='queued'`
    filter design decision 17 first suggested: a `deferred` item (postponed because it collided
    with a still-`pending` Run for the same prompt+model) must eventually be reclaimed once its
    pushed-forward `scheduled_for` arrives, or `deferred` would be a dead end the CHECK
    constraint on `run_queue.status` (migration 0030) never intended it to be.

    `FOR UPDATE SKIP LOCKED` is what makes this safe with more than one worker process: a second
    worker's identical query simply skips a row this one is already holding, rather than
    blocking on it or claiming it twice.
    """
    item = db.scalars(
        select(RunQueueItem)
        .where(RunQueueItem.status.in_(("queued", "deferred")), RunQueueItem.scheduled_for <= now)
        .order_by(RunQueueItem.priority.desc(), RunQueueItem.scheduled_for.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if item is None:
        return None

    item.status = "leased"
    item.leased_by = worker_name
    item.leased_until = now + timedelta(minutes=lease_minutes)
    item.attempts += 1
    if item.started_at is None:
        item.started_at = now
    db.commit()
    return item


def release_expired_leases(db: Session, *, now: datetime) -> int:
    """Return a dead worker's claim to the queue so another worker can pick it up.

    Scoped to `run_id IS NULL` only (design decision 13): once a worker has written a Run's id
    onto the item, the adapter may already have been called, so simply requeuing it here could
    call the provider a second time for the same occurrence. That case belongs exclusively to
    `reconcile_interrupted_runs` below, which marks the outcome unknown instead of retrying it.
    """
    result = db.execute(
        update(RunQueueItem)
        .where(
            RunQueueItem.status == "leased",
            RunQueueItem.leased_until < now,
            RunQueueItem.run_id.is_(None),
        )
        .values(status="queued", leased_by=None, leased_until=None)
    )
    db.commit()
    return result.rowcount


def reconcile_interrupted_runs(db: Session, *, now: datetime) -> int:
    """Resolve a Run left `pending` because the worker (or a manual request) died mid-call.

    Not scoped to scheduled runs — a manual trigger killed mid-request has exactly the same
    "adapter outcome unknown" problem, and the worker is the one long-lived process positioned
    to sweep for it periodically. Deliberately records the *uncertainty*, never a guessed
    success or silent retry (design decision 13) — the alternative, calling the adapter again,
    risks paying for the same occurrence twice with no way to tell.
    """
    stale_cutoff = now - timedelta(minutes=30)
    stale_runs = db.scalars(select(Run).where(Run.status == "pending", Run.started_at < stale_cutoff)).all()

    for run in stale_runs:
        run.status = "error"
        run.error_message = "worker interrupted; provider call outcome unknown"
        run.finished_at = now

        queue_item = db.scalar(select(RunQueueItem).where(RunQueueItem.run_id == run.id))
        if queue_item is not None and queue_item.status not in ("done", "error", "cancelled"):
            queue_item.status = "error"
            queue_item.last_error = run.error_message
            queue_item.finished_at = now

    db.commit()
    return len(stale_runs)


#: Terminal states worth retrying — 'done' already succeeded, and 'queued'/'leased'/'deferred'
#: aren't history yet (docs/TASKS_SCHEDULER.md T7).
RETRYABLE_STATUSES = ("error", "skipped", "cancelled")


def create_retry(db: Session, *, original: RunQueueItem, now: datetime) -> RunQueueItem:
    """Insert a new `run_queue` row that retries `original` (docs/TASKS_SCHEDULER.md T7) — same

    schedule/target/priority, a fresh `scheduled_for=now`, and `retry_of_id` pointing back at the
    original. Never modifies `original` itself: history is never rewritten (NFR-6), so a retried
    item's own terminal status stays exactly what it actually was. No `batch_id` — a retry is a
    new, standalone occurrence, not a continuation of whatever window the original came from (that
    window has already fully resolved).

    Raises `ValueError` if `original.status` isn't in `RETRYABLE_STATUSES` — validated here rather
    than only in the router, so this precondition is exercised by a plain service-level test like
    every other function in this module, not only through an HTTP round trip. The router still
    owns turning that into a translated `AppError`, per this app's usual service/router split.
    """
    if original.status not in RETRYABLE_STATUSES:
        raise ValueError(f"queue item {original.id} has status {original.status!r}, not retryable")
    retry = RunQueueItem(
        schedule_id=original.schedule_id,
        source=original.source,
        batch_id=None,
        client_id=original.client_id,
        prompt_id=original.prompt_id,
        model_id=original.model_id,
        market_id=original.market_id,
        persona_id=original.persona_id,
        scheduled_for=now,
        priority=original.priority,
        status="queued",
        retry_of_id=original.id,
    )
    db.add(retry)
    db.commit()
    db.refresh(retry)
    return retry


def cancel_queued_item(db: Session, *, item: RunQueueItem, now: datetime) -> None:
    """Mark a still-`queued` item 'cancelled' (docs/TASKS_SCHEDULER.md T7).

    Raises `ValueError` for anything other than `status == 'queued'` — most importantly a
    `leased` item, which is in a worker's hands right now and must never be touched here.
    """
    if item.status != "queued":
        raise ValueError(f"queue item {item.id} has status {item.status!r}, not cancellable")
    item.status = "cancelled"
    item.finished_at = now
    db.commit()


def retry_all_errors(db: Session, *, search: str, now: datetime) -> int:
    """Retry every current `status='error'` item whose prompt text or client name matches

    `search` (case-insensitive substring; every error item when `search` is empty) — the History
    page's "Retry all errors" bulk action, deliberately scoped to exactly the filter the user is
    looking at (T7: "hromadná akce nesáhne mimo filtr") rather than every error in the system.
    Returns how many were retried.
    """
    query = (
        select(RunQueueItem)
        .join(Prompt, RunQueueItem.prompt_id == Prompt.id)
        .join(Client, RunQueueItem.client_id == Client.id)
        .where(RunQueueItem.status == "error")
    )
    if search:
        like = f"%{search}%"
        query = query.where(or_(Prompt.text.ilike(like), Client.name.ilike(like)))
    items = db.scalars(query).all()
    for item in items:
        create_retry(db, original=item, now=now)
    return len(items)
