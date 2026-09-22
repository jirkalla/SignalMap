"""The in-app channel (docs/TASKS_SCHEDULER.md T8) — the only channel that exists today.

"Delivery" is trivial: a notification is visible in the UI as soon as its row exists with
`status='sent'`, since /schedules reads directly from `notification_outbox` (app/routers/
schedules.py). There is no separate rendering/formatting step and no backlog-suppression concern
the way a future SMTP channel would have (see app/notifications/base.py's `send()` docstring) —
an old `pending` row is exactly as valid to show as a brand new one.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.notification import NotificationOutbox


class InAppChannel:
    """Marks a notification visible on /schedules. No external call, so nothing here can fail in

    a way worth catching — a bare assignment either works or the process is broken regardless.
    """

    def send(self, db: Session, notification: NotificationOutbox) -> None:
        notification.status = "sent"
        notification.sent_at = datetime.now(timezone.utc)
