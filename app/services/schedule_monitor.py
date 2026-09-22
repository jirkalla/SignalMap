"""Aggregation queries for the /schedules monitoring page (docs/TASKS_SCHEDULER.md T6) — worker
liveness, the active queue, and batched history.

Mirrors app/services/ops_dashboard.py's own split from its router: every aggregate here is SQL
(GROUP BY/COUNT/MIN/MAX), never a Python loop over full `RunQueueItem` rows (same discipline that
module's design decision 4 documents). `find_overlapping_schedules`/`find_all_overlap_counts`
(the "Rozvrhy" view's overlap badge) stay in app/routers/schedules.py instead of here — they need
that module's `_resolve_target`, a router-private helper this service has no reason to depend on.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIModel, Client, Persona, Prompt
from app.models.notification import WorkerHeartbeat
from app.models.schedule import RunQueueItem

# The worker writes its heartbeat once per loop iteration — a 5s sleep plus up to ~28s for one
# provider call (app/worker.py's own docstring, matching docker-compose.yaml's stop_grace_period
# comment) — so 60s is a 2x safety margin before /schedules calls it stale, not the raw loop
# interval itself (design decision 19).
WORKER_STALE_THRESHOLD_SECONDS = 60

_ACTIVE_STATUSES = ("queued", "leased", "deferred")
_TERMINAL_STATUSES = ("done", "error", "skipped", "cancelled")


@dataclass
class WorkerStatus:
    """One `worker_heartbeats` row with staleness already resolved against `now` — the /schedules

    status bar exists because a stopped worker and an empty queue look identical otherwise
    (design decision 19).
    """

    worker_name: str
    last_seen_at: datetime
    seconds_since: float
    is_stale: bool
    dry_run: bool


def worker_statuses(db: Session, *, now: datetime) -> list[WorkerStatus]:
    """Every known worker's liveness, most recently seen first. Empty only when no worker has

    ever written a heartbeat at all (the router renders that as its own "never seen" state,
    distinct from a specific named worker having gone stale).
    """
    rows = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())).all()
    return [
        WorkerStatus(
            worker_name=row.worker_name,
            last_seen_at=row.last_seen_at,
            seconds_since=(now - row.last_seen_at).total_seconds(),
            is_stale=(now - row.last_seen_at).total_seconds() > WORKER_STALE_THRESHOLD_SECONDS,
            dry_run=row.dry_run,
        )
        for row in rows
    ]


def format_duration_short(t, total_seconds: float) -> str:
    """"8 s" / "5 min" / "3 h 20 min" — the worker status bar and the oldest-queued-item age both

    need this two-tier unit choice; kept in one place rather than each composing its own.
    """
    total_seconds = max(0, int(total_seconds))
    if total_seconds < 60:
        return t("common.duration_seconds").format(s=total_seconds)
    minutes, _ = divmod(total_seconds, 60)
    if minutes < 60:
        return t("common.duration_minutes").format(m=minutes)
    hours, minutes = divmod(minutes, 60)
    return t("common.duration_hours_minutes").format(h=hours, m=minutes)


@dataclass
class QueueRow:
    """One active (`queued`/`leased`/`deferred`) run_queue item — backs the "Fronta" view."""

    id: int
    client_name: str
    prompt_text: str
    model_name: str
    persona_label: str
    scheduled_for: datetime
    priority: int
    status: str
    leased_by: str | None
    leased_until: datetime | None
    schedule_id: int | None


def active_queue_rows(db: Session) -> list[QueueRow]:
    """Everything waiting or in flight, in the exact order `claim_next` would take it next —

    priority DESC, scheduled_for ASC, the same order as `idx_run_queue_claim_order` — so this view
    answers "what happens next", not just "what's in the table" (design decision 20's priority
    number is finally visible here).
    """
    rows = db.execute(
        select(RunQueueItem, Client.name, Prompt.text, AIModel, Persona.label)
        .join(Client, RunQueueItem.client_id == Client.id)
        .join(Prompt, RunQueueItem.prompt_id == Prompt.id)
        .join(AIModel, RunQueueItem.model_id == AIModel.id)
        .join(Persona, RunQueueItem.persona_id == Persona.id)
        .where(RunQueueItem.status.in_(_ACTIVE_STATUSES))
        .order_by(RunQueueItem.priority.desc(), RunQueueItem.scheduled_for.asc())
    ).all()
    return [
        QueueRow(
            id=item.id,
            client_name=client_name,
            prompt_text=prompt_text,
            model_name=model.display_name or model.model_name,
            persona_label=persona_label,
            scheduled_for=item.scheduled_for,
            priority=item.priority,
            status=item.status,
            leased_by=item.leased_by,
            leased_until=item.leased_until,
            schedule_id=item.schedule_id,
        )
        for item, client_name, prompt_text, model, persona_label in rows
    ]


def oldest_queued_age_seconds(db: Session, *, now: datetime) -> float | None:
    """Age of the longest-waiting `queued` item — the first signal that the worker isn't keeping

    up, ahead of raw queue depth (a deep-but-fast-draining queue is fine; an old-but-shallow one
    isn't). `None` when nothing is queued.
    """
    oldest = db.scalar(select(func.min(RunQueueItem.queued_at)).where(RunQueueItem.status == "queued"))
    return (now - oldest).total_seconds() if oldest is not None else None


@dataclass
class QueueSummary:
    """Counts by status among the active queue — backs the "Fronta" view's KPI cards."""

    queued_count: int
    leased_count: int
    deferred_count: int


def queue_summary(db: Session) -> QueueSummary:
    """One grouped COUNT query, not a derivation from `active_queue_rows`'s already-fetched list —

    keeps the KPI cards correct even if a future caller only wants the summary, not the full row
    list.
    """
    counts = dict(
        db.execute(
            select(RunQueueItem.status, func.count(RunQueueItem.id))
            .where(RunQueueItem.status.in_(_ACTIVE_STATUSES))
            .group_by(RunQueueItem.status)
        ).all()
    )
    return QueueSummary(
        queued_count=counts.get("queued", 0),
        leased_count=counts.get("leased", 0),
        deferred_count=counts.get("deferred", 0),
    )


@dataclass
class HistoryItem:
    """One run_queue item inside a history batch's expandable detail."""

    id: int
    client_name: str
    prompt_text: str
    model_name: str
    persona_label: str
    status: str
    finished_at: datetime | None
    run_id: int | None
    schedule_id: int | None
    message: str | None


@dataclass
class HistoryBatch:
    """One row of the "Historie" view — one or more run_queue items sharing a `batch_id` (design

    decision 32's prompt-set fanout), collapsed to counts-by-status with the individual items
    behind a disclosure, so a 225-item prompt-set window reads as one row, not 225 (per the note
    recorded against SCH-5b in docs/PROMPTS_SCHEDULER.md). An item with no `batch_id` (every
    prompt-level schedule, one item per window) is simply its own batch of one.
    """

    key: str
    scheduled_for: datetime
    total: int
    done_count: int
    error_count: int
    skipped_count: int
    cancelled_count: int
    items: list[HistoryItem]


def history_batches(
    db: Session, *, status_filter: str, search: str, page: int, page_size: int = 50
) -> tuple[list[HistoryBatch], bool]:
    """(batches, has_next_page) for the "Historie" view, most recent first.

    `status_filter` is one of "all"/"errors"/"skipped" — the latter two keep only batches with at
    least one item in that status (a mixed batch still shows all its own items, see below).
    `search` matches `Prompt.text`/`Client.name` (case-insensitive substring) and is applied
    per-item, before grouping — a search narrows what's shown inside a batch too, not just which
    batches qualify, so the counts in a matched batch's summary line always agree with the rows
    behind its disclosure.

    Two queries, not N+1: the first groups by batch key and paginates the GROUPS, not the raw rows
    — a 225-row prompt-set window must count as one page entry, not 225 of them pushing everything
    else off the page. The second fetches every item belonging to just this page's batch keys in
    one round trip, reusing the identical key expression (and the same search filter) so it lines
    up with the first query's grouping without needing to separately track which keys are real
    UUIDs versus synthetic single-item keys. `has_next_page` comes from fetching one extra group
    per page rather than a separate COUNT(*) query.
    """
    batch_key = func.coalesce(cast(RunQueueItem.batch_id, String), func.concat("item-", cast(RunQueueItem.id, String)))
    error_count_expr = func.count(RunQueueItem.id).filter(RunQueueItem.status == "error")
    skipped_count_expr = func.count(RunQueueItem.id).filter(RunQueueItem.status == "skipped")

    def _apply_search(query):
        if search:
            like = f"%{search}%"
            return query.where(or_(Prompt.text.ilike(like), Client.name.ilike(like)))
        return query

    group_query = (
        select(
            batch_key.label("batch_key"),
            func.max(RunQueueItem.scheduled_for),
            func.count(RunQueueItem.id),
            func.count(RunQueueItem.id).filter(RunQueueItem.status == "done"),
            error_count_expr,
            skipped_count_expr,
            func.count(RunQueueItem.id).filter(RunQueueItem.status == "cancelled"),
        )
        .select_from(RunQueueItem)
        .join(Client, RunQueueItem.client_id == Client.id)
        .join(Prompt, RunQueueItem.prompt_id == Prompt.id)
        .where(RunQueueItem.status.in_(_TERMINAL_STATUSES))
    )
    group_query = _apply_search(group_query).group_by("batch_key")
    if status_filter == "errors":
        group_query = group_query.having(error_count_expr > 0)
    elif status_filter == "skipped":
        group_query = group_query.having(skipped_count_expr > 0)
    group_query = (
        group_query.order_by(func.max(RunQueueItem.scheduled_for).desc()).limit(page_size + 1).offset((page - 1) * page_size)
    )

    group_rows = db.execute(group_query).all()
    has_next = len(group_rows) > page_size
    group_rows = group_rows[:page_size]
    if not group_rows:
        return [], False

    keys = [row[0] for row in group_rows]
    item_query = (
        select(batch_key.label("batch_key"), RunQueueItem, Client.name, Prompt.text, AIModel, Persona.label)
        .join(Client, RunQueueItem.client_id == Client.id)
        .join(Prompt, RunQueueItem.prompt_id == Prompt.id)
        .join(AIModel, RunQueueItem.model_id == AIModel.id)
        .join(Persona, RunQueueItem.persona_id == Persona.id)
        .where(batch_key.in_(keys))
    )
    item_rows = db.execute(_apply_search(item_query).order_by(RunQueueItem.scheduled_for.asc())).all()

    items_by_key: dict[str, list[HistoryItem]] = {key: [] for key in keys}
    for key, item, client_name, prompt_text, model, persona_label in item_rows:
        items_by_key[key].append(
            HistoryItem(
                id=item.id,
                client_name=client_name,
                prompt_text=prompt_text,
                model_name=model.display_name or model.model_name,
                persona_label=persona_label,
                status=item.status,
                finished_at=item.finished_at,
                run_id=item.run_id,
                schedule_id=item.schedule_id,
                message=item.last_error if item.status == "error" else item.skip_reason,
            )
        )

    return (
        [
            HistoryBatch(
                key=row[0],
                scheduled_for=row[1],
                total=row[2],
                done_count=row[3],
                error_count=row[4],
                skipped_count=row[5],
                cancelled_count=row[6],
                items=items_by_key[row[0]],
            )
            for row in group_rows
        ],
        has_next,
    )


@dataclass
class ScheduleHealthTile:
    """One recent window's outcome for a schedule — one tile in the "Historie" health strip

    (design decision 37). `outcome` is one of 'error'/'done'/'skipped'/'cancelled'.
    """

    outcome: str
    scheduled_for: datetime


def schedule_health(db: Session, *, limit_per_schedule: int = 10) -> dict[int, list[ScheduleHealthTile]]:
    """{schedule_id: [tile, ...]} for the last `limit_per_schedule` windows of every schedule that

    has at least one terminal `run_queue` batch — the "Historie" health strip's data (design
    decision 37), oldest first (so the strip reads left-to-right as time moving forward, the same
    convention a git contribution graph or an uptime status bar uses).

    A window's outcome is all-or-nothing, the same rule a CI build uses for its overall status:
    any `error` item makes the whole window `error`, even if most of it succeeded; otherwise any
    `done` item makes it `done`; otherwise `skipped` if the window had nothing but skips (a
    dry-run window, or one where nothing was active); `cancelled` only when that's literally all
    there is (T7, not wired up as of this writing).

    One SQL round trip: batches are grouped exactly as `history_batches` groups them, then
    ranked per schedule via `ROW_NUMBER() OVER (PARTITION BY schedule_id ORDER BY scheduled_for
    DESC)` and cut to the top N — no Python loop over the full `run_queue` table, and no N+1
    per-schedule queries.
    """
    batch_key = func.coalesce(cast(RunQueueItem.batch_id, String), func.concat("item-", cast(RunQueueItem.id, String)))
    error_count_expr = func.count(RunQueueItem.id).filter(RunQueueItem.status == "error")
    done_count_expr = func.count(RunQueueItem.id).filter(RunQueueItem.status == "done")
    skipped_count_expr = func.count(RunQueueItem.id).filter(RunQueueItem.status == "skipped")

    grouped = (
        select(
            RunQueueItem.schedule_id.label("schedule_id"),
            batch_key.label("batch_key"),
            func.max(RunQueueItem.scheduled_for).label("scheduled_for"),
            error_count_expr.label("error_count"),
            done_count_expr.label("done_count"),
            skipped_count_expr.label("skipped_count"),
        )
        .where(RunQueueItem.status.in_(_TERMINAL_STATUSES), RunQueueItem.schedule_id.is_not(None))
        .group_by(RunQueueItem.schedule_id, "batch_key")
        .subquery()
    )
    ranked = select(
        grouped,
        func.row_number().over(partition_by=grouped.c.schedule_id, order_by=grouped.c.scheduled_for.desc()).label("rank"),
    ).subquery()

    rows = db.execute(
        select(ranked)
        .where(ranked.c.rank <= limit_per_schedule)
        .order_by(ranked.c.schedule_id, ranked.c.scheduled_for.asc())
    ).all()

    result: dict[int, list[ScheduleHealthTile]] = {}
    for row in rows:
        if row.error_count > 0:
            outcome = "error"
        elif row.done_count > 0:
            outcome = "done"
        elif row.skipped_count > 0:
            outcome = "skipped"
        else:
            outcome = "cancelled"
        result.setdefault(row.schedule_id, []).append(
            ScheduleHealthTile(outcome=outcome, scheduled_for=row.scheduled_for)
        )
    return result
