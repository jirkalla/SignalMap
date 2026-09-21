"""notify() — the one place any scheduler event becomes a `notification_outbox` row (docs/
TASKS_SCHEDULER.md T8, design decision 25) — plus the two periodic checks (`schedule.expiring_soon`,
`budget.threshold_exceeded`) that don't originate from a single event but from observing state.

Five event types are wired up in this branch: `schedule.run_failed` (app/worker.py, after a queue
item's final failed attempt), `schedule.window_skipped` (app/services/queue.py, one summary row
per ticker pass, not one per skipped window), `worker.stale` (app/routers/schedules.py, detected
by the web app when /schedules is viewed — a dead worker cannot report its own death), and the two
functions below. `quota.exceeded` (T10) and `schedule.owner_deactivated` (T9) are deliberately not
wired here — those tasks aren't built yet, and will call the same `notify()` once they are.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.notification import NotificationOutbox
from app.models.prompt import PromptSet
from app.models.schedule import RunSchedule
from app.notifications import CHANNELS
from app.services.cost import client_month_to_date_spend
from app.utils import current_prompt_version

logger = logging.getLogger(__name__)

# How long a worker.stale notification stays "fresh" before /schedules is willing to fire another
# one for the same worker — long enough not to re-notify on every single page view of a stale
# worker, short enough that a long outage still gets periodic re-alerts, not exactly one ever.
_WORKER_STALE_COOLDOWN = timedelta(minutes=60)

# How close to its own end a schedule has to be before schedule.expiring_soon fires (design
# decision 31) — whichever of the two end conditions (date or occurrence count) is set.
_EXPIRING_SOON_DAYS = 7
_EXPIRING_SOON_OCCURRENCES = 3


def notify(db: Session, event_type: str, payload: dict, *, recipient_user_id: int | None = None) -> NotificationOutbox:
    """Write one `notification_outbox` row and immediately attempt delivery through every

    registered channel (app/notifications/CHANNELS) — always writes and logs, even with zero
    channels registered or every channel failing, since the outbox row itself is the durable
    record (design decision 25); a channel is only a way to surface what's already there.
    """
    notification = NotificationOutbox(
        event_type=event_type, payload=payload, recipient_user_id=recipient_user_id, status="pending"
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    logger.info("notification %s (%s): %s", notification.id, event_type, payload)

    for channel in CHANNELS:
        try:
            channel.send(db, notification)
        except Exception:  # noqa: BLE001 - a channel violating its own no-raise contract
            logger.exception("channel %s failed to send notification %s", type(channel).__name__, notification.id)
            notification.status = "failed"
            notification.last_error = f"{type(channel).__name__} raised unexpectedly"
    db.commit()
    return notification


def _already_notified(db: Session, *, event_type: str, payload_key: str, payload_value: str, since: datetime | None = None) -> bool:
    """Whether a `notification_outbox` row already exists for this (event_type, payload field)

    pair — the dedup check every periodic/observational notifier below uses instead of a new
    tracking column, since `payload` already holds whatever identifies "the same occurrence".
    """
    query = select(NotificationOutbox.id).where(
        NotificationOutbox.event_type == event_type,
        NotificationOutbox.payload[payload_key].astext == payload_value,
    )
    if since is not None:
        query = query.where(NotificationOutbox.created_at >= since)
    return db.scalar(query.limit(1)) is not None


def _schedule_target_label(db: Session, schedule: RunSchedule) -> str:
    """Short display label for a schedule's target, for notification text — a display-only

    sliver of app/routers/schedules.py's `_resolve_target` (which also needs the resolved
    ACTIVE-PROMPTS LIST for fanout, not just a label, and is router-private on purpose). Kept as
    its own small lookup here rather than importing that router helper, so this service has no
    dependency on a router module.
    """
    if schedule.target_type == "prompt":
        prompt = current_prompt_version(db, schedule.target_id)
        return prompt.text[:60] if prompt is not None else "(deleted prompt)"
    if schedule.target_type == "prompt_set":
        prompt_set = db.get(PromptSet, schedule.target_id)
        return prompt_set.name if prompt_set is not None else "(deleted prompt set)"
    return "(unknown target)"


def check_expiring_schedules(db: Session, *, now: datetime) -> None:
    """Notify once per schedule when it's within 7 days or 3 occurrences of its own end (design

    decision 31) — the guard against a schedule's data collection quietly stopping unnoticed.
    Idempotent forever per schedule, not on a cooldown: a schedule only approaches its own end
    once in its life, so "already notified, ever, for this schedule" is the correct dedup.
    """
    active_schedules = db.scalars(select(RunSchedule).where(RunSchedule.is_active.is_(True))).all()
    for schedule in active_schedules:
        days_left = (schedule.ends_on - now.date()).days if schedule.ends_on is not None else None
        occurrences_left = (
            schedule.max_occurrences - schedule.occurrences_count if schedule.max_occurrences is not None else None
        )
        is_expiring = (days_left is not None and days_left <= _EXPIRING_SOON_DAYS) or (
            occurrences_left is not None and occurrences_left <= _EXPIRING_SOON_OCCURRENCES
        )
        if not is_expiring:
            continue
        if _already_notified(db, event_type="schedule.expiring_soon", payload_key="schedule_id", payload_value=str(schedule.id)):
            continue
        notify(
            db,
            "schedule.expiring_soon",
            {
                "schedule_id": schedule.id,
                "client_id": schedule.client_id,
                "client_name": schedule.client.name,
                "target_type": schedule.target_type,
                "target_label": _schedule_target_label(db, schedule),
                "ends_on": schedule.ends_on.isoformat() if schedule.ends_on else None,
                "days_left": days_left,
                "occurrences_left": occurrences_left,
            },
        )


def check_budget_thresholds(db: Session, *, now: datetime) -> None:
    """Notify once per calendar month when a client's month-to-date spend crosses its own

    `monthly_budget_usd` (design decision 33) — never blocks anything (that's the daily run-limit's
    job, T10), it only informs. Idempotent per (client, month): next month's spend starts back at
    zero, so a client that crosses again next month is meant to be notified again.
    """
    month_start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
    clients_with_budget = db.scalars(select(Client).where(Client.monthly_budget_usd.is_not(None))).all()
    for client in clients_with_budget:
        spend = client_month_to_date_spend(db, client_id=client.id, month_start=month_start)
        if spend is None or spend < float(client.monthly_budget_usd):
            continue
        if _already_notified(
            db, event_type="budget.threshold_exceeded", payload_key="client_id", payload_value=str(client.id), since=month_start
        ):
            continue
        notify(
            db,
            "budget.threshold_exceeded",
            {"client_id": client.id, "client_name": client.name, "spend_usd": round(spend, 2), "budget_usd": float(client.monthly_budget_usd)},
        )


def notify_worker_stale(db: Session, *, worker_name: str, seconds_since: float, now: datetime) -> None:
    """Fire `worker.stale` for `worker_name`, unless one already fired within the last hour

    (`_WORKER_STALE_COOLDOWN`) — called from app/routers/schedules.py on every /schedules view,
    so without this a stale worker would generate one notification per page load.
    """
    if _already_notified(
        db, event_type="worker.stale", payload_key="worker_name", payload_value=worker_name, since=now - _WORKER_STALE_COOLDOWN
    ):
        return
    notify(db, "worker.stale", {"worker_name": worker_name, "seconds_since": round(seconds_since)})
