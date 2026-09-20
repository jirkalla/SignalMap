"""Timezone-aware recurrence calculation for run schedules (docs/TASKS_SCHEDULER.md T1).

`compute_next_run_at` and `max_end_date` are pure functions — no `datetime.now()`/`utcnow()`
call and no database access — so every calendar/DST edge case is testable with an explicit
`after` instead of waiting for the real date to roll around (design decision 10). The ticker
(app/services/queue.py, added in a later task) is the only caller that ever supplies a real
"now"; everything here just transforms the `after` it's given.

`next_run_at` must always be derived from the occurrence's own due date, never from when the
ticker actually ran (design decision 9) — passing the previous result back in as `after` and
getting the same wall-clock time out (not a time drifted by however late the ticker was) is
exactly what tests/test_scheduling_recurrence.py's drift test checks.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.models.schedule import RunSchedule

# Generous bound on how many calendar days ahead to scan for the next matching occurrence.
# A monthly schedule needs at most ~31 days, weekly at most 7 — this is deliberately far larger
# than either needs, so a bug that breaks date-matching fails loudly with RuntimeError instead of
# silently returning a wrong-but-plausible date.
_MAX_SEARCH_DAYS = 400


def _is_matching_date(schedule: "RunSchedule", candidate: date) -> bool:
    """Whether `candidate` is a day this schedule's frequency fires on, ignoring time of day."""
    if schedule.frequency == "daily":
        return True
    if schedule.frequency == "weekly":
        # ISO weekday: Monday=1 ... Sunday=7, matching docs/TASKS_SCHEDULER.md's own notation
        # (days_of_week={1,4} = Po+Čt = Monday+Thursday).
        return candidate.isoweekday() in schedule.days_of_week
    if schedule.frequency == "monthly":
        # Design decision 8: a day_of_month beyond the month's length (e.g. 31 in February)
        # clamps to that month's last day, rather than skipping the month entirely.
        last_day_of_month = calendar.monthrange(candidate.year, candidate.month)[1]
        return candidate.day == min(schedule.day_of_month, last_day_of_month)
    raise ValueError(f"unknown schedule frequency: {schedule.frequency!r}")


def compute_next_run_at(schedule: "RunSchedule", *, after: datetime) -> datetime | None:
    """The next UTC instant this schedule fires strictly after `after`, or `None` if the
    schedule has already run its course (design decision 31).

    `after` must be timezone-aware; the search is done in the schedule's own local timezone
    (design decision 7) and the result converted back to UTC at the end.

    Ambiguous local times (the one hour that occurs twice when clocks fall back in autumn) and
    nonexistent local times (the one hour skipped when clocks spring forward) are both resolved
    by fixing `fold=0` when attaching the timezone (design decision 8): for an ambiguous time
    this picks the first, still-summer-time occurrence rather than letting the two occurrences
    silently collide into the same `scheduled_for` later on; for a nonexistent time, Python does
    not raise — it silently computes an offset as if standard time still applied, which lands
    exactly one DST-delta after the requested wall-clock time once converted back to local (e.g.
    2027-03-28 02:30 Europe/Prague, which does not exist, becomes 01:30 UTC = 03:30 local). Both
    outcomes are deliberate, tested behavior, not an accident of Python's default `fold=0`.
    """
    tz = ZoneInfo(schedule.timezone)
    after_local = after.astimezone(tz)

    candidate_date = max(after_local.date(), schedule.starts_on)
    candidate_local: datetime | None = None
    for _ in range(_MAX_SEARCH_DAYS):
        if _is_matching_date(schedule, candidate_date):
            naive = datetime.combine(candidate_date, schedule.time_of_day)
            local_attempt = naive.replace(tzinfo=tz, fold=0)
            if local_attempt > after_local:
                candidate_local = local_attempt
                break
        candidate_date += timedelta(days=1)
    else:
        raise RuntimeError(
            f"no matching occurrence found for schedule frequency {schedule.frequency!r} "
            f"within {_MAX_SEARCH_DAYS} days of {after!r}"
        )

    if schedule.max_occurrences is not None and schedule.occurrences_count >= schedule.max_occurrences:
        return None
    if schedule.ends_on is not None and candidate_date > schedule.ends_on:
        return None

    return candidate_local.astimezone(timezone.utc)


def max_end_date(frequency: str, *, starts_on: date) -> date:
    """The furthest `ends_on` a new schedule of this frequency may pick (design decision 31).

    Sized so that daily/weekly/monthly schedules all cap out at roughly the same number of
    unattended occurrences (~26-30) rather than the same number of months — a fixed calendar cap
    would let a daily schedule rack up 365 occurrences before anyone has to look at it again.
    """
    if frequency == "daily":
        return starts_on + timedelta(days=30)
    if frequency == "weekly":
        return _add_months(starts_on, 6)
    if frequency == "monthly":
        return _add_months(starts_on, 12)
    raise ValueError(f"unknown schedule frequency: {frequency!r}")


def _add_months(start: date, months: int) -> date:
    """`start` plus a whole number of calendar months, clamping the day to the target month's
    length (e.g. 2026-01-31 + 1 month = 2026-02-28) — the same clamping rule `_is_matching_date`
    applies to monthly schedules, kept consistent rather than reimplemented ad hoc. No dependency
    on `dateutil` (design decision 30): plain month/year arithmetic plus stdlib `calendar`.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
