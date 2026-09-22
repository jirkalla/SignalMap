"""Shared interface every notification channel (app/notifications/<channel>.py) implements.

`notify()` (app/services/notifications.py) never delivers directly — always through a channel
implementing `send()` below, mirroring app/adapters/<provider>.py's own "caller never touches the
concrete implementation directly" discipline (docs/TASKS_SCHEDULER.md design decision 25): adding
an email channel later means adding a file plus one registry entry (app/notifications/__init__.py),
never rewriting every `notify()` call site.
"""

from typing import Protocol

from sqlalchemy.orm import Session

from app.models.notification import NotificationOutbox


class NotificationChannel(Protocol):
    """Interface every notification channel implements."""

    def send(self, db: Session, notification: NotificationOutbox) -> None:
        """Deliver `notification` and record the outcome on the row itself.

        Mutates `notification` (`status`/`sent_at`/`last_error`) but never commits — `notify()`
        commits once after every channel has had a chance to run, so one channel's failure can't
        leave another channel's successful delivery half-written.

        Must not let an ordinary delivery failure propagate as an exception — catch it, set
        `status='failed'` and `last_error`, and return, so one channel's outage never stops
        another registered channel from running. (`notify()` also wraps this call in a backstop
        try/except for a channel that violates this contract, but a channel should not rely on
        that backstop.)

        A channel activated after a period of not existing must not attempt to deliver the
        backlog that accumulated while it didn't exist — old rows should be marked `suppressed`
        instead of sent, the first time that channel runs. Sending a burst of days-old
        notifications the moment a channel comes online is worse than not sending them: it makes
        the notification stream noisy right when someone is first learning to trust it. This
        applies to a future SMTP channel (not implemented in this branch — see `.env.example`'s
        SMTP_* keys); the in-app channel has no backlog problem, since `status='sent'` already
        just means "visible on /schedules", not "emailed", so an old pending row is exactly as
        valid to show as a new one.

        A channel that sends failure notifications one at a time (rather than batching into a
        periodic digest) will re-alert on every single occurrence of a flapping condition — for
        an email channel specifically, that means one email per failed run, which past a handful
        gets filtered to a spam folder and stops being read at all. A future channel that emails
        should batch `schedule.run_failed`/`schedule.window_skipped` events into a daily digest
        rather than sending one message per `notify()` call; the in-app channel doesn't have this
        problem, since the /schedules list is inherently a batched view already.
        """
        ...
