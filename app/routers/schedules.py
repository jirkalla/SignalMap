"""Run schedule CRUD (docs/TASKS_SCHEDULER.md T5) — the recurrence rule half of the scheduler;

see app/models/schedule.py for why that's a separate concern from `run_queue`/`Run`. Only
`target_type='prompt'` exists here — a whole-prompt-set schedule is T5b, built on top of this
form rather than beside it.

Every mutating route is gated by `require_role("admin", "editor")` directly (never just a
hidden UI element — docs/TASKS_PHASE6.md design decision 4); `can_schedule()`
(app/templating.py) is the template-side mirror of that same rule, kept as one function so a
future `users.can_schedule` column changes only that one place (design decision 22).
"""

from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import User
from app.models.schedule import RunSchedule
from app.routers.prompts import _get_prompt_or_404, _runnable_model_groups
from app.services.cost import average_historical_cost, current_prices
from app.services.scheduling import ScheduleOccurrenceInput, compute_next_run_at, max_end_date, upcoming_occurrences
from app.templating import get_t, render
from app.utils import current_prompt_version, market_options, persona_options

router = APIRouter(prefix="/schedules", tags=["schedules"])

_editor_or_admin = [Depends(require_role("admin", "editor"))]

_FREQUENCIES = ("daily", "weekly", "monthly")
# ISO weekday numbers (Monday=1 ... Sunday=7), matching compute_next_run_at's own convention
# (app/services/scheduling.py) and docs/TASKS_SCHEDULER.md's own notation ({1,4} = Mon+Thu).
_WEEKDAY_I18N_KEYS = [
    (1, "schedules.weekday_1"),
    (2, "schedules.weekday_2"),
    (3, "schedules.weekday_3"),
    (4, "schedules.weekday_4"),
    (5, "schedules.weekday_5"),
    (6, "schedules.weekday_6"),
    (7, "schedules.weekday_7"),
]


def _parse_optional_int(value: str) -> int | None:
    """An HTML number input left blank submits `""`, not an absent field — FastAPI/Pydantic

    reject `""` outright for an `int | None` Form/Query parameter (it's not a valid int, and
    Pydantic doesn't treat an empty string as "missing"). Every optional number field in this
    router (day_of_month, max_occurrences) is declared as `str` and parsed through this instead.
    """
    return int(value) if value else None


def _parse_optional_date(value: str) -> date | None:
    """Same reasoning as `_parse_optional_int`, for `ends_on`/`starts_on`."""
    return date.fromisoformat(value) if value else None


def _get_schedule_or_404(db: Session, request: Request, schedule_id: int) -> RunSchedule:
    schedule = db.get(RunSchedule, schedule_id)
    if schedule is None:
        raise AppError("schedule_not_found", get_t(request)("errors.schedule_not_found"), status_code=404)
    return schedule


def schedules_for_prompt(db: Session, root_prompt_id: int) -> list[RunSchedule]:
    """Every schedule targeting this prompt's lineage — shown as a partial on the prompt detail

    page (app/templates/schedules/_list.html), for whoever can see the prompt at all (read-only
    for a viewer; only `can_schedule()` gates the mutating controls in the template itself).
    """
    return db.scalars(
        select(RunSchedule)
        .where(RunSchedule.target_type == "prompt", RunSchedule.target_id == root_prompt_id)
        .order_by(RunSchedule.created_at.desc())
    ).all()


def schedules_for_client(db: Session, client_id: int) -> list[RunSchedule]:
    """Every schedule belonging to this client, across all of its prompts — shown as a partial

    on the client detail page.
    """
    return db.scalars(
        select(RunSchedule).where(RunSchedule.client_id == client_id).order_by(RunSchedule.created_at.desc())
    ).all()


def _weekday_options(t) -> list[tuple[int, str]]:
    return [(value, t(key)) for value, key in _WEEKDAY_I18N_KEYS]


def schedule_summary_text(t, schedule: RunSchedule) -> str:
    """One-line human description of a schedule's recurrence rule, e.g. "Daily at 06:00" or

    "Mon, Thu at 06:00" — shown on the schedule list partial (app/templates/schedules/_list.html)
    on both the prompt and client detail pages. The rule itself is always shown in the
    schedule's own zone, never the browser's (design decision 28) — `time_of_day` already IS
    that local wall-clock value, so this needs no timezone conversion at all, unlike
    `next_run_at` (a real UTC instant, converted client-side by base.html's localizeTimes()).
    """
    time_str = schedule.time_of_day.strftime("%H:%M")
    if schedule.frequency == "daily":
        return t("schedules.summary_daily").format(time=time_str)
    if schedule.frequency == "weekly":
        days = ", ".join(t(key) for value, key in _WEEKDAY_I18N_KEYS if value in (schedule.days_of_week or []))
        return t("schedules.summary_weekly").format(days=days, time=time_str)
    if schedule.frequency == "monthly":
        return t("schedules.summary_monthly").format(day=schedule.day_of_month, time=time_str)
    return time_str


def _market_options_with_prompt_default(db: Session, t) -> list[tuple[str, str]]:
    """Market <select> options for the schedule form, with a leading empty option meaning "the

    prompt's own market" (`RunSchedule.market_id = NULL`, design decision — see the schema
    comment in app/models/schedule.py) — every other option is a real market id, overriding it.
    """
    return [("", t("schedules.market_prompt_default"))] + [(str(mid), label) for mid, label in market_options(db)]


def _max_end_dates(starts_on: date) -> dict[str, str]:
    return {frequency: max_end_date(frequency, starts_on=starts_on).isoformat() for frequency in _FREQUENCIES}


def _occurrence_input(schedule: RunSchedule) -> ScheduleOccurrenceInput:
    return ScheduleOccurrenceInput(
        frequency=schedule.frequency,
        days_of_week=schedule.days_of_week,
        day_of_month=schedule.day_of_month,
        time_of_day=schedule.time_of_day,
        timezone=schedule.timezone,
        starts_on=schedule.starts_on,
        ends_on=schedule.ends_on,
        max_occurrences=schedule.max_occurrences,
        occurrences_count=schedule.occurrences_count,
    )


def _schedule_form_context(
    request: Request,
    db: Session,
    *,
    title: str,
    action: str,
    cancel_url: str,
    root_prompt_id: int,
    schedule: RunSchedule | None,
) -> dict:
    t = get_t(request)
    prompt = current_prompt_version(db, root_prompt_id)
    starts_on = schedule.starts_on if schedule else date.today()
    return {
        "title": title,
        "action": action,
        "cancel_url": cancel_url,
        "prompt": prompt,
        "client": prompt.prompt_set.client,
        "root_prompt_id": root_prompt_id,
        "schedule": schedule,
        "model_groups": _runnable_model_groups(db),
        "selected_model_ids": set(schedule.model_ids) if schedule else set(),
        "personas": persona_options(db),
        "selected_persona_ids": set(schedule.persona_ids) if schedule else set(),
        "markets": _market_options_with_prompt_default(db, t),
        "weekdays": _weekday_options(t),
        "selected_days_of_week": set(schedule.days_of_week) if schedule and schedule.days_of_week else set(),
        "end_type": "occurrences" if schedule and schedule.max_occurrences is not None else "date",
        "max_end_dates": _max_end_dates(starts_on),
        "time_of_day_value": schedule.time_of_day.strftime("%H:%M") if schedule else "06:00",
    }


@router.get("/new", dependencies=_editor_or_admin)
def new_schedule_form(request: Request, prompt_id: int = Query(...), db: Session = Depends(get_db)):
    """Render the schedule-creation form for one prompt (design decision 6: stored against the

    prompt's lineage root, resolved here from whichever version the user was looking at).
    """
    prompt = _get_prompt_or_404(db, request, prompt_id)
    root_prompt_id = prompt.root_prompt_id or prompt.id
    t = get_t(request)
    return render(
        request,
        "schedules/form.html",
        _schedule_form_context(
            request,
            db,
            title=t("schedules.create_title"),
            action="/schedules",
            cancel_url=f"/prompts/{prompt_id}",
            root_prompt_id=root_prompt_id,
            schedule=None,
        ),
    )


def _parse_time_of_day(value: str) -> time:
    return time.fromisoformat(value)


def _resolve_end(
    t, end_type: str, ends_on: date | None, max_occurrences: int | None, *, frequency: str, starts_on: date
) -> tuple[date | None, int | None]:
    """Enforce "exactly one end" server-side (design decision 31) regardless of what the client

    sent — the form's own JS keeps the unused field empty in normal use, but this is the actual
    guarantee, matching the database CHECK constraint (migration 0030) rather than trusting it.
    Raises AppError, not an inline re-rendered form error: reaching this branch at all means the
    client-side toggle was bypassed, which is a different class of problem than an ordinary
    mistake like a duplicate name.
    """
    if end_type == "date":
        if ends_on is None:
            raise AppError("schedule_end_required", t("errors.schedule_end_required"), status_code=422)
        if ends_on > max_end_date(frequency, starts_on=starts_on):
            raise AppError("schedule_end_too_far", t("errors.schedule_end_too_far"), status_code=422)
        return ends_on, None
    if end_type == "occurrences":
        if not max_occurrences or max_occurrences < 1:
            raise AppError("schedule_end_required", t("errors.schedule_end_required"), status_code=422)
        return None, max_occurrences
    raise AppError("schedule_end_required", t("errors.schedule_end_required"), status_code=422)


def _validate_frequency_fields(t, frequency: str, days_of_week: list[int], day_of_month: int | None) -> None:
    if frequency not in _FREQUENCIES:
        raise AppError("schedule_invalid_frequency", t("errors.schedule_invalid_frequency"), status_code=422)
    if frequency == "weekly" and not days_of_week:
        raise AppError("schedule_days_of_week_required", t("errors.schedule_days_of_week_required"), status_code=422)
    if frequency == "monthly" and not day_of_month:
        raise AppError("schedule_day_of_month_required", t("errors.schedule_day_of_month_required"), status_code=422)


@router.post("", dependencies=_editor_or_admin)
def create_schedule(
    request: Request,
    target_id: int = Form(..., description="The prompt lineage's root id (design decision 6)."),
    frequency: Literal["daily", "weekly", "monthly"] = Form(...),
    time_of_day: str = Form(..., description="Local wall-clock time, HH:MM, in the schedule's own timezone."),
    days_of_week: list[int] = Form(default=[], description="ISO weekday numbers (1=Mon..7=Sun); required for weekly."),
    day_of_month: str = Form("", description="1-31, clamped to the shorter month; required for monthly."),
    market_id: str = Form("", description="Empty means the prompt's own market."),
    model_ids: list[int] = Form(default=[], description="At least one model to run against."),
    persona_ids: list[int] = Form(default=[], description="At least one persona to frame the question as."),
    priority: int = Form(100, description="Scheduler priority weight (design decision 20)."),
    end_type: Literal["date", "occurrences"] = Form(...),
    ends_on: str = Form(""),
    max_occurrences: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Create a schedule against one prompt lineage (design decisions 6, 7, 20, 31, 34)."""
    t = get_t(request)
    prompt = current_prompt_version(db, target_id)
    if prompt is None:
        raise AppError("prompt_not_found", t("errors.prompt_not_found"), status_code=404)

    day_of_month = _parse_optional_int(day_of_month)
    ends_on = _parse_optional_date(ends_on)
    max_occurrences = _parse_optional_int(max_occurrences)

    _validate_frequency_fields(t, frequency, days_of_week, day_of_month)
    if not model_ids:
        raise AppError("schedule_models_required", t("errors.schedule_models_required"), status_code=422)
    if not persona_ids:
        raise AppError("schedule_personas_required", t("errors.schedule_personas_required"), status_code=422)

    starts_on = date.today()
    resolved_ends_on, resolved_max_occurrences = _resolve_end(
        t, end_type, ends_on, max_occurrences, frequency=frequency, starts_on=starts_on
    )

    schedule = RunSchedule(
        client_id=prompt.prompt_set.client_id,
        target_type="prompt",
        target_id=target_id,
        model_ids=model_ids,
        market_id=int(market_id) if market_id else None,
        persona_ids=persona_ids,
        frequency=frequency,
        days_of_week=days_of_week if frequency == "weekly" else None,
        day_of_month=day_of_month if frequency == "monthly" else None,
        time_of_day=_parse_time_of_day(time_of_day),
        # Set explicitly, not left to the column's SQLAlchemy-side `default=` (design decision
        # 7: never shown in the form, always Europe/Prague) — that default only applies once
        # this row is actually flushed, but compute_next_run_at needs a real value on the
        # in-memory object right below, before this schedule is ever added to the session.
        timezone="Europe/Prague",
        starts_on=starts_on,
        ends_on=resolved_ends_on,
        max_occurrences=resolved_max_occurrences,
        # Same reasoning as `timezone` above — compute_next_run_at reads this before any flush
        # ever applies the column's own `default=0`.
        occurrences_count=0,
        priority=priority,
        created_by_user_id=user.id,
    )
    schedule.next_run_at = compute_next_run_at(
        schedule, after=datetime.combine(starts_on, time.min, tzinfo=timezone.utc)
    )
    db.add(schedule)
    db.commit()

    target_url = f"/prompts/{prompt.id}"
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = target_url
        return response
    return RedirectResponse(url=target_url, status_code=303)


@router.get("/{schedule_id}/edit", dependencies=_editor_or_admin)
def edit_schedule_form(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    schedule = _get_schedule_or_404(db, request, schedule_id)
    t = get_t(request)
    return render(
        request,
        "schedules/form.html",
        _schedule_form_context(
            request,
            db,
            title=t("schedules.edit_title"),
            action=f"/schedules/{schedule_id}/edit",
            cancel_url=f"/prompts/{current_prompt_version(db, schedule.target_id).id}",
            root_prompt_id=schedule.target_id,
            schedule=schedule,
        ),
    )


@router.post("/{schedule_id}/edit", dependencies=_editor_or_admin)
def update_schedule(
    request: Request,
    schedule_id: int,
    frequency: Literal["daily", "weekly", "monthly"] = Form(...),
    time_of_day: str = Form(...),
    days_of_week: list[int] = Form(default=[]),
    day_of_month: str = Form(""),
    market_id: str = Form(""),
    model_ids: list[int] = Form(default=[]),
    persona_ids: list[int] = Form(default=[]),
    priority: int = Form(100),
    end_type: Literal["date", "occurrences"] = Form(...),
    ends_on: str = Form(""),
    max_occurrences: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Update a schedule's rule. Never changes `target_type`/`target_id`/`client_id`/`starts_on`

    — retargeting a schedule to a different prompt is a delete-and-recreate, not an edit.
    `next_run_at` is always recomputed from `now`, not `starts_on`, since an edited rule takes
    effect going forward, never retroactively.
    """
    t = get_t(request)
    schedule = _get_schedule_or_404(db, request, schedule_id)

    day_of_month = _parse_optional_int(day_of_month)
    ends_on = _parse_optional_date(ends_on)
    max_occurrences = _parse_optional_int(max_occurrences)

    _validate_frequency_fields(t, frequency, days_of_week, day_of_month)
    if not model_ids:
        raise AppError("schedule_models_required", t("errors.schedule_models_required"), status_code=422)
    if not persona_ids:
        raise AppError("schedule_personas_required", t("errors.schedule_personas_required"), status_code=422)

    resolved_ends_on, resolved_max_occurrences = _resolve_end(
        t, end_type, ends_on, max_occurrences, frequency=frequency, starts_on=schedule.starts_on
    )

    schedule.frequency = frequency
    schedule.days_of_week = days_of_week if frequency == "weekly" else None
    schedule.day_of_month = day_of_month if frequency == "monthly" else None
    schedule.time_of_day = _parse_time_of_day(time_of_day)
    schedule.market_id = int(market_id) if market_id else None
    schedule.model_ids = model_ids
    schedule.persona_ids = persona_ids
    schedule.priority = priority
    schedule.ends_on = resolved_ends_on
    schedule.max_occurrences = resolved_max_occurrences
    schedule.updated_by_user_id = user.id
    if schedule.is_active:
        schedule.next_run_at = compute_next_run_at(schedule, after=datetime.now(timezone.utc))
        if schedule.next_run_at is None:
            schedule.is_active = False
            schedule.inactive_reason = "completed"
    db.commit()

    prompt = current_prompt_version(db, schedule.target_id)
    target_url = f"/prompts/{prompt.id}"
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = target_url
        return response
    return RedirectResponse(url=target_url, status_code=303)


@router.post("/{schedule_id}/toggle", dependencies=_editor_or_admin)
def toggle_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db), user: User = Depends(current_active_user)):
    """Pause an active schedule, or resume a paused one.

    Resuming recomputes `next_run_at` from `now` — a schedule paused for a week must not fire a
    burst of "missed" windows the instant it's turned back on (that's exactly what the grace
    period in app/services/queue.py guards against for the ticker; recomputing from `now` here
    means there's nothing stale for it to even encounter). A schedule that already ran its
    course can't be resumed this way — decision 31's "prodloužit" (extend) action is a separate,
    later feature, not implied by a plain resume.
    """
    t = get_t(request)
    schedule = _get_schedule_or_404(db, request, schedule_id)

    if schedule.is_active:
        schedule.is_active = False
        schedule.inactive_reason = "user"
        schedule.next_run_at = None
    else:
        if schedule.inactive_reason == "completed":
            raise AppError("schedule_already_completed", t("errors.schedule_already_completed"), status_code=409)
        schedule.is_active = True
        schedule.inactive_reason = None
        schedule.next_run_at = compute_next_run_at(schedule, after=datetime.now(timezone.utc))
        if schedule.next_run_at is None:
            schedule.is_active = False
            schedule.inactive_reason = "completed"
    schedule.updated_by_user_id = user.id
    db.commit()

    prompt = current_prompt_version(db, schedule.target_id)
    return RedirectResponse(url=f"/prompts/{prompt.id}", status_code=303)


@router.post("/{schedule_id}/delete", dependencies=_editor_or_admin)
def delete_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    """Delete a schedule's rule row. Its `run_queue` history (design decision 27) is untouched

    — `run_queue.schedule_id` has no `ON DELETE` behavior, so history from a deleted schedule
    stays queryable by id even though the rule itself is gone.
    """
    schedule = _get_schedule_or_404(db, request, schedule_id)
    prompt = current_prompt_version(db, schedule.target_id)
    db.delete(schedule)
    db.commit()
    return RedirectResponse(url=f"/prompts/{prompt.id}", status_code=303)


@router.get("/preview", dependencies=_editor_or_admin)
def preview_occurrences(
    request: Request,
    frequency: Literal["daily", "weekly", "monthly"] = Query(...),
    time_of_day: str = Query(...),
    days_of_week: list[int] = Query(default=[]),
    day_of_month: str = Query(""),
    model_ids: list[int] = Query(default=[]),
    persona_ids: list[int] = Query(default=[]),
    end_type: Literal["date", "occurrences"] = Query(...),
    ends_on: str = Query(""),
    max_occurrences: str = Query(""),
    starts_on: str = Query(""),
    occurrences_count: int = Query(0),
    root_prompt_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Live HTMX partial: the next five occurrences and a cost estimate for the form's current,

    unsaved values (design decisions 29 and 34) — never touches any `RunSchedule` row. Silently
    tolerant of an incomplete/invalid form (e.g. weekly with no day checked yet, or both end
    fields empty while the user is still choosing) — this is a live preview, not a submission, so
    it renders "pick a valid combination" rather than a 422 for every incomplete intermediate
    keystroke.
    """
    t = get_t(request)
    day_of_month = _parse_optional_int(day_of_month)
    ends_on = _parse_optional_date(ends_on)
    max_occurrences = _parse_optional_int(max_occurrences)
    effective_starts_on = _parse_optional_date(starts_on) or date.today()
    incomplete = (
        frequency not in _FREQUENCIES
        or (frequency == "weekly" and not days_of_week)
        or (frequency == "monthly" and not day_of_month)
        or not model_ids
        or not persona_ids
        or (end_type == "date" and ends_on is None)
        or (end_type == "occurrences" and not max_occurrences)
    )
    if incomplete:
        return render(request, "schedules/_occurrence_preview.html", {"incomplete": True})

    occurrence_input = ScheduleOccurrenceInput(
        frequency=frequency,
        days_of_week=days_of_week if frequency == "weekly" else None,
        day_of_month=day_of_month if frequency == "monthly" else None,
        time_of_day=_parse_time_of_day(time_of_day),
        timezone="Europe/Prague",
        starts_on=effective_starts_on,
        ends_on=ends_on if end_type == "date" else None,
        max_occurrences=max_occurrences if end_type == "occurrences" else None,
        occurrences_count=occurrences_count,
    )
    after = datetime.combine(effective_starts_on, time.min, tzinfo=timezone.utc)
    upcoming, total_occurrences = upcoming_occurrences(occurrence_input, after=after)

    prices = current_prices(db, model_ids)
    per_window_cost = 0.0
    per_window_cost_known = True
    for model_id in model_ids:
        model_avg = average_historical_cost(db, root_prompt_id=root_prompt_id, model_id=model_id)
        if model_avg is None:
            per_window_cost_known = False
            continue
        per_window_cost += model_avg * len(persona_ids)

    return render(
        request,
        "schedules/_occurrence_preview.html",
        {
            "incomplete": False,
            "upcoming": upcoming,
            "run_count_per_window": len(model_ids) * len(persona_ids),
            "total_occurrences": total_occurrences,
            "per_window_cost": per_window_cost if per_window_cost_known else None,
            "total_cost": (
                per_window_cost * total_occurrences
                if per_window_cost_known and total_occurrences is not None
                else None
            ),
        },
    )
