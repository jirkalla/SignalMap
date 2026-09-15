"""Shared date-range shorthand resolver (docs/TASKS_OPS_DASHBOARD.md T1, design decision 9).

Moved out of app/services/dashboard.py (behavior unchanged) so the ops dashboard can build on the
same `DashboardRange`/`range_bounds` independently of the client-facing dashboard module, per
design decision 1 — the two dashboards are separate features that happen to share this one
building block, not one dashboard depending on the other.
"""

from datetime import datetime, timedelta, timezone
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
