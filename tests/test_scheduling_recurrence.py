"""Table-driven tests for app/services/scheduling.py (docs/TASKS_SCHEDULER.md T1).

Expected UTC instants are written as literals, never computed via `compute_next_run_at` itself
(design decision 10) — where a DST offset is involved, the literal was cross-checked once against
plain `zoneinfo` arithmetic (Python's own stdlib, not the function under test), not derived from
this module. `RunSchedule` instances here are plain in-memory objects, never persisted — same
approach as `tests/test_cost.py`'s `_model()` helper, since `compute_next_run_at`/`max_end_date`
only ever read already-loaded scalar attributes.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models.schedule import RunSchedule
from app.services.scheduling import compute_next_run_at, max_end_date

PRAGUE = ZoneInfo("Europe/Prague")
UTC = timezone.utc


def _schedule(
    *,
    frequency: str,
    time_of_day: time,
    starts_on: date,
    ends_on: date | None = None,
    max_occurrences: int | None = None,
    occurrences_count: int = 0,
    days_of_week: list[int] | None = None,
    day_of_month: int | None = None,
    timezone_name: str = "Europe/Prague",
) -> RunSchedule:
    if ends_on is None and max_occurrences is None:
        # Every real schedule has exactly one end (design decision 31, enforced by a DB CHECK
        # constraint that never runs against these in-memory instances) — default one in here so
        # tests that don't care about the end condition don't have to think about it either.
        max_occurrences = 1000
    return RunSchedule(
        frequency=frequency,
        time_of_day=time_of_day,
        timezone=timezone_name,
        starts_on=starts_on,
        ends_on=ends_on,
        max_occurrences=max_occurrences,
        occurrences_count=occurrences_count,
        days_of_week=days_of_week,
        day_of_month=day_of_month,
    )


def test_weekly_monday_thursday_pattern():
    # 2026-09-14 is a Monday, 2026-09-17 a Thursday, 2026-09-21 the following Monday.
    schedule = _schedule(
        frequency="weekly",
        days_of_week=[1, 4],
        time_of_day=time(6, 0),
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
    )

    monday = compute_next_run_at(schedule, after=datetime(2026, 9, 14, 3, 0, tzinfo=UTC))
    assert monday == datetime(2026, 9, 14, 4, 0, tzinfo=UTC)

    thursday = compute_next_run_at(schedule, after=monday)
    assert thursday == datetime(2026, 9, 17, 4, 0, tzinfo=UTC)

    next_monday = compute_next_run_at(schedule, after=thursday)
    assert next_monday == datetime(2026, 9, 21, 4, 0, tzinfo=UTC)


def test_weekly_schedule_from_a_day_not_in_days_of_week():
    # Wednesday is in neither {Monday, Thursday} — the search must skip forward to Thursday.
    schedule = _schedule(
        frequency="weekly",
        days_of_week=[1, 4],
        time_of_day=time(6, 0),
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
    )

    result = compute_next_run_at(schedule, after=datetime(2026, 9, 16, 12, 0, tzinfo=UTC))

    assert result == datetime(2026, 9, 17, 4, 0, tzinfo=UTC)


def test_dst_fall_back_transition_changes_utc_offset():
    # 2026-10-25 is when Europe/Prague falls back from CEST (UTC+2) to CET (UTC+1).
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(6, 0),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )

    before_transition = compute_next_run_at(schedule, after=datetime(2026, 10, 22, 20, 0, tzinfo=UTC))
    assert before_transition == datetime(2026, 10, 23, 4, 0, tzinfo=UTC)  # still CEST

    after_transition = compute_next_run_at(schedule, after=datetime(2026, 10, 25, 20, 0, tzinfo=UTC))
    assert after_transition == datetime(2026, 10, 26, 5, 0, tzinfo=UTC)  # now CET


def test_dst_spring_forward_transition_changes_utc_offset():
    # 2027-03-28 is when Europe/Prague springs forward from CET (UTC+1) to CEST (UTC+2).
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(6, 0),
        starts_on=date(2027, 1, 1),
        ends_on=date(2027, 12, 31),
    )

    before_transition = compute_next_run_at(schedule, after=datetime(2027, 3, 25, 20, 0, tzinfo=UTC))
    assert before_transition == datetime(2027, 3, 26, 5, 0, tzinfo=UTC)  # still CET

    after_transition = compute_next_run_at(schedule, after=datetime(2027, 3, 29, 20, 0, tzinfo=UTC))
    assert after_transition == datetime(2027, 3, 30, 4, 0, tzinfo=UTC)  # now CEST


def test_ambiguous_fall_back_time_resolves_to_first_occurrence():
    # 2026-10-25 02:30 Europe/Prague occurs twice (clocks fall back at 03:00 CEST -> 02:00 CET).
    # fold=0 (design decision 8) picks the first occurrence, still on CEST (UTC+2) -> 00:30 UTC.
    # The second occurrence (fold=1, CET, UTC+1) would be 01:30 UTC -- never returned.
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(2, 30),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )

    result = compute_next_run_at(schedule, after=datetime(2026, 10, 24, 23, 0, tzinfo=UTC))

    assert result == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_nonexistent_spring_forward_time_does_not_raise():
    # 2027-03-28 02:30 Europe/Prague does not exist (clocks jump from 02:00 to 03:00 CET->CEST).
    # fold=0 computes the offset as if standard time (CET, UTC+1) still applied, landing on
    # 01:30 UTC -- which is 03:30 local once converted back, one DST-delta after the request.
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(2, 30),
        starts_on=date(2027, 1, 1),
        ends_on=date(2027, 12, 31),
    )

    result = compute_next_run_at(schedule, after=datetime(2027, 3, 27, 23, 0, tzinfo=UTC))

    assert result == datetime(2027, 3, 28, 1, 30, tzinfo=UTC)
    assert result.astimezone(PRAGUE) == datetime(2027, 3, 28, 3, 30, tzinfo=PRAGUE)


def test_daily_schedule_crosses_the_year_boundary():
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(23, 30),
        starts_on=date(2026, 1, 1),
        ends_on=date(2027, 12, 31),
    )

    # `after` is exactly 2026-12-31 23:30 Europe/Prague (CET, UTC+1) -- strictly-after semantics
    # mean that instant itself is excluded, so the next occurrence rolls into the new year.
    result = compute_next_run_at(schedule, after=datetime(2026, 12, 31, 22, 30, tzinfo=UTC))

    assert result == datetime(2027, 1, 1, 22, 30, tzinfo=UTC)


def test_monthly_schedule_clamps_day_31_to_shorter_months():
    schedule = _schedule(
        frequency="monthly",
        day_of_month=31,
        time_of_day=time(6, 0),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )

    january = compute_next_run_at(schedule, after=datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    assert january == datetime(2026, 1, 31, 5, 0, tzinfo=UTC)

    february = compute_next_run_at(schedule, after=january)
    assert february == datetime(2026, 2, 28, 5, 0, tzinfo=UTC)  # 2026 is not a leap year

    # Clamping to February's last day must not get the schedule permanently "stuck" on day 28 --
    # March has 31 days again, and by then Europe/Prague has also entered CEST.
    march = compute_next_run_at(schedule, after=february)
    assert march == datetime(2026, 3, 31, 4, 0, tzinfo=UTC)


def test_drift_repeated_calls_preserve_wall_clock_time():
    # Design decision 9: next_run_at must come from the occurrence's own due date, never from
    # `now()` -- feeding each result back in as `after` must keep landing on the same 06:00 local
    # wall-clock time, not creep forward or backward across DST or repeated calls.
    schedule = _schedule(
        frequency="weekly",
        days_of_week=[1, 4],
        time_of_day=time(6, 0),
        starts_on=date(2026, 9, 1),
        ends_on=date(2027, 12, 31),
    )

    result = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    for _ in range(20):
        result = compute_next_run_at(schedule, after=result)
        local = result.astimezone(PRAGUE)
        assert (local.hour, local.minute) == (6, 0)
        assert local.isoweekday() in (1, 4)


def test_returns_none_once_max_occurrences_reached():
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(6, 0),
        starts_on=date(2026, 1, 1),
        max_occurrences=5,
        occurrences_count=4,
    )
    assert compute_next_run_at(schedule, after=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) is not None

    schedule.occurrences_count = 5
    assert compute_next_run_at(schedule, after=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) is None


def test_occurrence_exactly_on_ends_on_still_runs_but_not_after():
    schedule = _schedule(
        frequency="daily",
        time_of_day=time(6, 0),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 9, 20),
    )

    on_the_end_date = compute_next_run_at(schedule, after=datetime(2026, 9, 19, 20, 0, tzinfo=UTC))
    assert on_the_end_date == datetime(2026, 9, 20, 4, 0, tzinfo=UTC)

    past_the_end_date = compute_next_run_at(schedule, after=on_the_end_date)
    assert past_the_end_date is None


def test_max_end_date_daily_is_thirty_days():
    assert max_end_date("daily", starts_on=date(2026, 1, 1)) == date(2026, 1, 31)


def test_max_end_date_weekly_is_six_months():
    assert max_end_date("weekly", starts_on=date(2026, 1, 1)) == date(2026, 7, 1)


def test_max_end_date_monthly_is_twelve_months():
    assert max_end_date("monthly", starts_on=date(2026, 1, 1)) == date(2027, 1, 1)


def test_max_end_date_clamps_to_shorter_target_month():
    # 2026-08-31 plus 6 months would be "2027-02-31", which doesn't exist -- clamps to Feb 28.
    assert max_end_date("weekly", starts_on=date(2026, 8, 31)) == date(2027, 2, 28)
