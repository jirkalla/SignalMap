"""Cross-process request pacing, backed by the `throttle_state` table (code-review finding,

2026-09-30, docs/TASKS_CITATION_VERIFICATION.md) — `app/services/source_capture.py`'s per-domain
throttle and `app/services/archive_lookup.py`'s single-key throttle used to each keep their own
plain in-process module variable, which production's 4 worker processes (`docker compose --scale
worker=4`) could pace independently against, multiplying the documented per-target request rate
by however many processes happened to hit the same key at once.

`throttle()` atomically reserves the next available slot for `key` via one UPSERT — Postgres
serializes conflicting UPSERTs on the same primary key across connections/processes, so no lock is
held across the actual `sleep()` call: that happens in plain Python, after the reservation is
already committed, exactly like the original in-process version's own `sleep()` call.

Uses real wall-clock time (`datetime.now(timezone.utc)`), not a caller-supplied `now`, on purpose:
callers' `now` is typically fixed for an entire job's business-logic timestamps (e.g. `fetched_at`
on every `SourceDocument` a capture job writes), but real time keeps advancing between individual
HTTP calls within that same job — pacing against a frozen `now` would either never throttle or
always throttle the full interval, neither of which reflects reality. This matches the original
in-process implementation, which used `time.monotonic()`, not the injected `now`, for the exact
same reason.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.rate_limit import ThrottleState


def throttle(db: Session, key: str, *, interval_seconds: float, sleep: Callable[[float], None]) -> None:
    """Block (via `sleep`) until at least `interval_seconds` have passed since the last
    reservation for `key` — across every process sharing this database, not just this one.

    CAVEAT (code-review finding, 2026-09-30 round 2): this calls `db.commit()` on the CALLER's own
    `db` session as part of reserving the slot — deliberately (the reservation must be durable
    before `sleep()` blocks, or the row lock would serialize other processes behind this one's I/O,
    defeating the point of a shared pacer). That means calling `throttle()` also commits whatever
    ELSE is pending, uncommitted, on that same session at the time. Every current caller
    (`source_capture.capture_url`, `citation_verification._try_archive_fallback`) already commits
    its own row independently, so this is harmless today — but a future caller must not invoke
    `throttle()` in the middle of building up several related writes it intends to commit together;
    call it before starting that unit of work, not from inside it.
    """
    now = datetime.now(timezone.utc)
    interval = timedelta(seconds=interval_seconds)
    stmt = pg_insert(ThrottleState).values(key=key, next_available_at=now + interval)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ThrottleState.key],
        set_={"next_available_at": func.greatest(ThrottleState.next_available_at, now) + interval},
    ).returning(ThrottleState.next_available_at)
    next_available_at = db.scalar(stmt)
    db.commit()

    reserved_at = next_available_at - interval
    remaining = (reserved_at - now).total_seconds()
    if remaining > 0:
        sleep(remaining)
