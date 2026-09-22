"""WorkerHeartbeat and NotificationOutbox — scheduler liveness and the event outbox.

`WorkerHeartbeat` exists because the worker process listens on no port (docs/TASKS_SCHEDULER.md
design decision 19): without a row it writes on every loop iteration, a stopped worker and an
empty queue would look identical on `/schedules`, actively lying about whether anything is
actually running.

`NotificationOutbox` follows the same "add a channel, don't rewrite call sites" shape as
`app/adapters/<provider>.py` (design decision 25): an event is written here once, independent of
which delivery channels exist yet — today only the in-app channel reads it, and adding email later
means adding a channel module, not touching every place that raises an event.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class WorkerHeartbeat(Base):
    """One row per named worker process, overwritten in place on every loop iteration.

    Not evidence and not history — unlike `Run`/`RawResponse`/`Citation`, there is nothing here
    worth keeping once a newer heartbeat arrives, so this is a plain upsert target, keyed by
    `worker_name` rather than an autoincrement id.
    """

    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[str | None] = mapped_column(String(40))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)


class NotificationOutbox(Base):
    """One raised event, pending delivery through zero or more notification channels.

    `recipient_user_id = NULL` means "all admins" rather than a specific person — every event
    type T8 wires up (design decision 25) targets the whole admin/editor team, not one person, so
    every row today has it NULL; a per-user "unread" concept would need this to actually vary,
    which nothing currently produces. `payload` carries whatever the event type needs to render
    (e.g. schedule id, error message, occurrence count) — deliberately unstructured per event type
    rather than one column per possible field, the same reasoning as `Run.request_payload`/
    `RawResponse.raw_payload`.

    `status` has no DB-level CHECK constraint (unlike `RunQueueItem.status`) — legal values are
    `pending` (written, no channel has run yet), `sent` (a channel delivered it — for the in-app
    channel, T8, that just means "visible on /schedules" and doubles as "unread"), `read` (an
    analyst dismissed it in the shared in-app inbox — T8, application-level only, no schema
    change needed since the column was never constrained), `failed` (a channel raised), and
    `suppressed` (an accumulated backlog a channel deliberately chose not to deliver on
    activation — see app/notifications/base.py's `send()` docstring; nothing produces this yet,
    since the in-app channel has no backlog concept).
    """

    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipient: Mapped["User | None"] = relationship()
