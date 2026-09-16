"""Shared date-range shorthand resolver (docs/TASKS_OPS_DASHBOARD.md T1, design decision 9).

Moved out of app/services/dashboard.py (behavior unchanged) so the ops dashboard can build on the
same `DashboardRange`/`range_bounds` independently of the client-facing dashboard module, per
design decision 1 — the two dashboards are separate features that happen to share this one
building block, not one dashboard depending on the other.

`day_starts`/`week_starts`/`month_starts` (added later, code review finding) are the same reason:
both dashboards need to turn a resolved (date_from, date_to) into a gap-free bucket x-axis, so that
bucketing math lives here once rather than as two independently-maintained copies.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

DashboardRange = Literal["7d", "30d", "90d", "quarter", "all"]


def range_bounds(range_: DashboardRange) -> tuple[datetime | None, datetime | None]:
    """Translate a range shorthand into (date_from, date_to) bounds. `None, None` means "all time"."""
    now = datetime.now(timezone.utc)
    if range_ == "all":
        return None, None
    if range_ == "7d":
        return now - timedelta(days=7), now
    if range_ == "30d":
        return now - timedelta(days=30), now
    if range_ == "90d":
        return now - timedelta(days=90), now
    # "quarter": start of the current calendar quarter, not a rolling 90-day window — distinct from
    # "90d" even though the two are close in length most of the year.
    quarter_start_month = ((now.month - 1) // 3) * 3 + 1
    quarter_start = now.replace(month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return quarter_start, now


def day_starts(date_from: date, date_to: date) -> list[date]:
    """Every day between `date_from` and `date_to`, inclusive of both ends."""
    days, current = [], date_from
    while current <= date_to:
        days.append(current)
        current += timedelta(days=1)
    return days


def week_starts(date_from: date, date_to: date) -> list[date]:
    """Every Monday-aligned week start between `date_from` and `date_to`, inclusive of both ends'
    weeks — the full x-axis a time series chart needs, independent of which weeks actually have data.
    """
    start = date_from - timedelta(days=date_from.weekday())
    end = date_to - timedelta(days=date_to.weekday())
    weeks, current = [], start
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def month_starts(date_from: date, date_to: date) -> list[date]:
    """Every calendar month start between `date_from` and `date_to`, inclusive of both ends' months."""
    months, current = [], date_from.replace(day=1)
    end = date_to.replace(day=1)
    while current <= end:
        months.append(current)
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
    return months
