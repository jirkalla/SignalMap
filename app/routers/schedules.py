"""Run schedule CRUD (docs/TASKS_SCHEDULER.md T5/T5b) — the recurrence rule half of the
scheduler; see app/models/schedule.py for why that's a separate concern from `run_queue`/`Run`.

Two target types share one form and one set of routes: `target_type='prompt'` (one prompt
lineage, T5) and `target_type='prompt_set'` (every active prompt in a set, T5b, design decisions
24/32). `_resolve_target` is the one place that tells the two apart — everything downstream
(the form, the preview, redirects) reads through it rather than re-branching on `target_type`.

Every mutating route is gated by `require_role("admin", "editor")` directly (never just a
hidden UI element — docs/TASKS_PHASE6.md design decision 4); `can_schedule()`
(app/templating.py) is the template-side mirror of that same rule, kept as one function so a
future `users.can_schedule` column changes only that one place (design decision 22).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from itertools import groupby
from typing import Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Client, Persona, PromptSet, User
from app.models.prompt import Prompt
from app.models.schedule import RunSchedule
from app.routers.prompt_sets import _get_prompt_set_or_404
from app.routers.prompts import _get_prompt_or_404, _runnable_model_groups
from app.services.cost import average_historical_cost
from app.services.schedule_monitor import (
    active_queue_rows,
    format_duration_short,
    history_batches,
    oldest_queued_age_seconds,
    queue_summary,
    schedule_health,
    worker_statuses,
)
from app.services.scheduling import ScheduleOccurrenceInput, compute_next_run_at, max_end_date, upcoming_occurrences
from app.templating import get_t, render
from app.utils import current_prompt_version, market_options, persona_options

router = APIRouter(prefix="/schedules", tags=["schedules"])

_editor_or_admin = [Depends(require_role("admin", "editor"))]

# How many recent windows each schedule's health strip shows (design decision 37) — one place so
# the label ("Health, last N windows") and the query's own LIMIT never drift apart.
_HEALTH_STRIP_WINDOW_COUNT = 10

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


def schedules_for_prompt_set(db: Session, prompt_set_id: int) -> list[RunSchedule]:
    """Every schedule targeting this whole prompt set (design decisions 24, 32) — shown on the

    prompt set's own detail page, same partial as `schedules_for_prompt`.
    """
    return db.scalars(
        select(RunSchedule)
        .where(RunSchedule.target_type == "prompt_set", RunSchedule.target_id == prompt_set_id)
        .order_by(RunSchedule.created_at.desc())
    ).all()


def schedules_for_client(db: Session, client_id: int) -> list[RunSchedule]:
    """Every schedule belonging to this client, across all of its prompts and prompt sets —

    shown as a partial on the client detail page.
    """
    return db.scalars(
        select(RunSchedule).where(RunSchedule.client_id == client_id).order_by(RunSchedule.created_at.desc())
    ).all()


def all_schedules(db: Session) -> list[RunSchedule]:
    """Every schedule across every client, grouped by client name then soonest-next-run first —

    backs the /schedules "Rozvrhy" view (T6), unlike `schedules_for_client`, which scopes to one
    client for that client's own detail page. Ordered by `Client.name` first so the router can
    group rows with `itertools.groupby` (which, like Jinja's own `groupby` filter, requires
    pre-sorted input) without a second query; within a client, paused/completed schedules
    (`next_run_at IS NULL`) sink to the bottom rather than interleaving by a NULL that would
    otherwise sort first. Eager-loads `client` — the template reads its name on every row, and
    this page can list hundreds of schedules, so a lazy load there would be its own N+1 alongside
    the one `find_all_overlap_counts` already guards against.
    """
    return db.scalars(
        select(RunSchedule)
        .join(Client, RunSchedule.client_id == Client.id)
        .options(joinedload(RunSchedule.client))
        .order_by(Client.name.asc(), RunSchedule.is_active.desc(), RunSchedule.next_run_at.asc().nulls_last())
    ).all()


def _resolve_target(db: Session, target_type: str, target_id: int) -> tuple[str, Client, str, list[Prompt]] | None:
    """(display label, client, redirect url, active prompts) for a schedule's target — the one

    place `target_type` is ever branched on outside app/services/queue.py's own fanout (which
    has to re-resolve this itself at enqueue time, per design decision 6, rather than share this
    request-scoped helper). `None` if the target no longer exists (deleted prompt/prompt set).
    """
    if target_type == "prompt":
        prompt = current_prompt_version(db, target_id)
        if prompt is None:
            return None
        return prompt.text, prompt.prompt_set.client, f"/prompts/{prompt.id}", [prompt]
    if target_type == "prompt_set":
        prompt_set = db.get(PromptSet, target_id)
        if prompt_set is None:
            return None
        active_prompts = db.scalars(
            select(Prompt).where(
                Prompt.prompt_set_id == target_id,
                Prompt.is_current_version.is_(True),
                Prompt.is_active.is_(True),
            )
        ).all()
        return prompt_set.name, prompt_set.client, f"/prompt-sets/{prompt_set.id}", active_prompts
    raise ValueError(f"unknown schedule target_type: {target_type!r}")


def schedule_target_display(db: Session, schedule: RunSchedule) -> tuple[str, str, str | int]:
    """(short label, link url, type-specific detail) for a schedule's target, for the schedule

    list partial (app/templates/schedules/_list.html) on the client detail page, where one table
    mixes `target_type='prompt'` and `'prompt_set'` rows side by side — the detail is the owning
    prompt set's name for a `'prompt'` row, or the count of currently active prompts for a
    `'prompt_set'` row, letting the template show an icon plus an unambiguous type label rather
    than the small, easy-to-miss "(prompt_set)" suffix this replaced.
    """
    resolved = _resolve_target(db, schedule.target_type, schedule.target_id)
    if resolved is None:
        return "", "#", ""
    label, _client, url, prompts = resolved
    if schedule.target_type == "prompt_set":
        return label, url, len(prompts)
    return label, url, (prompts[0].prompt_set.name if prompts else "")


def _model_display_names(db: Session, model_ids: set[int]) -> list[str]:
    models = db.scalars(select(AIModel).where(AIModel.id.in_(model_ids))).all()
    return [m.display_name or m.model_name for m in models]


def _persona_display_names(db: Session, persona_ids: set[int]) -> list[str]:
    personas = db.scalars(select(Persona).where(Persona.id.in_(persona_ids))).all()
    return [p.label for p in personas]


@dataclass
class ScheduleOverlap:
    """One other active schedule whose resolved target shares at least one prompt, one model AND

    one persona with the combination being previewed (design decision 36) — a match on only one
    of those three dimensions is not reported (see find_overlapping_schedules).
    """

    schedule: RunSchedule
    label: str
    url: str
    shared_prompt_count: int
    shared_model_labels: list[str]
    shared_persona_labels: list[str]


def find_overlapping_schedules(
    db: Session,
    *,
    client_id: int,
    exclude_schedule_id: int | None,
    prompt_ids: list[int],
    model_ids: list[int],
    persona_ids: list[int],
) -> list[ScheduleOverlap]:
    """Other active schedules of this client that would run the same prompt on the same model for

    the same persona as the combination being previewed — design decision 36. Warn, never block:
    the two schedules that triggered this (one on a prompt, one on a prompt set containing it,
    same model/persona, different times) are both legitimate rows; nothing before this guarded
    against paying for the same content twice a day just because it arrived via two schedules.
    Scoped to one client — an overlap across two different clients' schedules isn't meaningful.
    A schedule with no active prompts left (a living prompt-set target, decision 18) simply
    contributes no shared prompts and never triggers a warning, same as a paused schedule.
    """
    query = select(RunSchedule).where(RunSchedule.client_id == client_id, RunSchedule.is_active.is_(True))
    if exclude_schedule_id is not None:
        query = query.where(RunSchedule.id != exclude_schedule_id)
    candidates = db.scalars(query).all()

    prompt_id_set, model_id_set, persona_id_set = set(prompt_ids), set(model_ids), set(persona_ids)
    overlaps = []
    for candidate in candidates:
        resolved = _resolve_target(db, candidate.target_type, candidate.target_id)
        if resolved is None:
            continue
        label, _client, url, candidate_prompts = resolved
        shared_prompts = prompt_id_set & {p.root_prompt_id or p.id for p in candidate_prompts}
        shared_models = model_id_set & set(candidate.model_ids)
        shared_personas = persona_id_set & set(candidate.persona_ids)
        if shared_prompts and shared_models and shared_personas:
            overlaps.append(
                ScheduleOverlap(
                    schedule=candidate,
                    label=label,
                    url=url,
                    shared_prompt_count=len(shared_prompts),
                    shared_model_labels=_model_display_names(db, shared_models),
                    shared_persona_labels=_persona_display_names(db, shared_personas),
                )
            )
    return overlaps


def find_all_overlap_counts(db: Session, schedules: list[RunSchedule]) -> dict[int, int]:
    """{schedule_id: count of other ACTIVE schedules it overlaps with} for every schedule in

    `schedules` at once — the /schedules "Rozvrhy" view's persistent badge (T6, extending SCH-5c's
    save-time warning to decision 36). Resolves each schedule's target exactly once, then compares
    every pair in memory, instead of calling `find_overlapping_schedules` once per row — that
    function itself resolves every OTHER schedule's target on every call, so doing that once per
    row on a page listing N schedules would be O(N) work repeated N times. Only active schedules
    are compared, and only within the same client, matching `find_overlapping_schedules`'s own
    scoping (design decision 36).
    """
    resolved = []
    for schedule in schedules:
        if not schedule.is_active:
            continue
        target = _resolve_target(db, schedule.target_type, schedule.target_id)
        if target is None:
            continue
        _label, client, _url, prompts = target
        resolved.append(
            (
                schedule.id,
                client.id,
                {p.root_prompt_id or p.id for p in prompts},
                set(schedule.model_ids),
                set(schedule.persona_ids),
            )
        )

    counts: dict[int, int] = {}
    for i, (schedule_id, client_id, prompt_ids, model_ids, persona_ids) in enumerate(resolved):
        for j, (_other_id, other_client_id, other_prompt_ids, other_model_ids, other_persona_ids) in enumerate(resolved):
            if i == j or client_id != other_client_id:
                continue
            if prompt_ids & other_prompt_ids and model_ids & other_model_ids and persona_ids & other_persona_ids:
                counts[schedule_id] = counts.get(schedule_id, 0) + 1
    return counts


def _redirect_url_for(db: Session, schedule: RunSchedule) -> str:
    """Where a mutating schedule route sends the browser back to — the target's own detail page,

    resolved fresh (not cached on the schedule) since the target's id is all `RunSchedule`
    stores.
    """
    resolved = _resolve_target(db, schedule.target_type, schedule.target_id)
    if resolved is None:
        return "/clients"
    return resolved[2]


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


def _schedule_form_context(
    request: Request,
    db: Session,
    *,
    title: str,
    action: str,
    cancel_url: str,
    target_type: str,
    target_id: int,
    schedule: RunSchedule | None,
) -> dict:
    t = get_t(request)
    resolved = _resolve_target(db, target_type, target_id)
    if resolved is None:
        raise AppError("prompt_not_found", t("errors.prompt_not_found"), status_code=404)
    target_label, client, _redirect_url, prompts = resolved
    starts_on = schedule.starts_on if schedule else date.today()
    return {
        "title": title,
        "action": action,
        "cancel_url": cancel_url,
        "target_type": target_type,
        "target_id": target_id,
        "target_label": target_label,
        "client": client,
        "prompt_count": len(prompts),
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
def new_schedule_form(
    request: Request,
    prompt_id: int | None = Query(None, description="Target one prompt lineage (design decision 6)."),
    prompt_set_id: int | None = Query(None, description="Target every active prompt in a set (design decision 32)."),
    db: Session = Depends(get_db),
):
    """Render the schedule-creation form, targeting either one prompt (resolved to its lineage

    root) or a whole prompt set — exactly one of `prompt_id`/`prompt_set_id` is expected.
    """
    t = get_t(request)
    if prompt_id is not None:
        prompt = _get_prompt_or_404(db, request, prompt_id)
        target_type, target_id, cancel_url = "prompt", prompt.root_prompt_id or prompt.id, f"/prompts/{prompt_id}"
    elif prompt_set_id is not None:
        prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
        target_type, target_id, cancel_url = "prompt_set", prompt_set.id, f"/prompt-sets/{prompt_set_id}"
    else:
        raise AppError("schedule_target_required", t("errors.schedule_target_required"), status_code=422)

    return render(
        request,
        "schedules/form.html",
        _schedule_form_context(
            request,
            db,
            title=t("schedules.create_title"),
            action="/schedules",
            cancel_url=cancel_url,
            target_type=target_type,
            target_id=target_id,
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
    target_type: Literal["prompt", "prompt_set"] = Form(
        ..., description="'prompt' targets one lineage (design decision 6); 'prompt_set' every active prompt in a set (design decision 32)."
    ),
    target_id: int = Form(..., description="Root prompt id or prompt set id, matching target_type."),
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
    """Create a schedule against one prompt lineage or a whole prompt set (design decisions 6,

    7, 20, 24, 31, 32, 34).
    """
    t = get_t(request)
    resolved = _resolve_target(db, target_type, target_id)
    if resolved is None:
        raise AppError("prompt_not_found", t("errors.prompt_not_found"), status_code=404)
    _label, client, redirect_url, _prompts = resolved

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
        client_id=client.id,
        target_type=target_type,
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

    target_url = redirect_url
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
            cancel_url=_redirect_url_for(db, schedule),
            target_type=schedule.target_type,
            target_id=schedule.target_id,
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

    target_url = _redirect_url_for(db, schedule)
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

    return RedirectResponse(url=_redirect_url_for(db, schedule), status_code=303)


@router.post("/{schedule_id}/delete", dependencies=_editor_or_admin)
def delete_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    """Delete a schedule's rule row. Its `run_queue` history (design decision 27) is untouched

    — `run_queue.schedule_id` has no `ON DELETE` behavior, so history from a deleted schedule
    stays queryable by id even though the rule itself is gone.
    """
    schedule = _get_schedule_or_404(db, request, schedule_id)
    target_url = _redirect_url_for(db, schedule)
    db.delete(schedule)
    db.commit()
    return RedirectResponse(url=target_url, status_code=303)


def _estimate_window_cost(
    db: Session, prompts: list[Prompt], model_ids: list[int], persona_count: int
) -> tuple[float | None, int, int]:
    """(estimated USD cost of one window, combinations with history, total combinations).

    Sums `average_historical_cost` over every (prompt, model) combination this window fans out
    across, times `persona_count` (design decisions 29, 32, 34). A combination with no run
    history yet is simply left out of the sum rather than blanking the whole estimate — SCH-5's
    original single-prompt version went all-or-nothing, which would show "unknown" forever for
    a freshly-created 25-prompt set where most combinations have never run once. The caller
    shows both numbers so "unknown" and "known but partial" stay visibly different.
    """
    total = 0.0
    known_combos = 0
    total_combos = 0
    for prompt in prompts:
        root_id = prompt.root_prompt_id or prompt.id
        for model_id in model_ids:
            total_combos += 1
            average = average_historical_cost(db, root_prompt_id=root_id, model_id=model_id)
            if average is not None:
                total += average * persona_count
                known_combos += 1
    return (total if known_combos else None), known_combos, total_combos


@router.get("/preview", dependencies=_editor_or_admin)
def preview_occurrences(
    request: Request,
    target_type: Literal["prompt", "prompt_set"] = Query(...),
    target_id: int = Query(...),
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
    schedule_id: str = Query("", description="The schedule being edited, if any — excluded from its own overlap check (design decision 36)."),
    db: Session = Depends(get_db),
):
    """Live HTMX partial: the next five occurrences and a cost estimate for the form's current,

    unsaved values (design decisions 29, 32 and 34) — never touches any `RunSchedule` row.
    Silently tolerant of an incomplete/invalid form (e.g. weekly with no day checked yet, or
    both end fields empty while the user is still choosing) — this is a live preview, not a
    submission, so it renders "pick a valid combination" rather than a 422 for every incomplete
    intermediate keystroke.
    """
    schedule_id = _parse_optional_int(schedule_id)
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

    resolved = _resolve_target(db, target_type, target_id)
    if resolved is None:
        return render(request, "schedules/_occurrence_preview.html", {"incomplete": True})
    _label, client, _redirect_url, prompts = resolved
    if not prompts:
        # A prompt_set with no active prompts right now (design decision 18: "a living list") —
        # a real, valid state, not an error, but there is nothing to estimate or fan out yet.
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

    per_window_cost, known_combos, total_combos = _estimate_window_cost(db, prompts, model_ids, len(persona_ids))

    overlaps = find_overlapping_schedules(
        db,
        client_id=client.id,
        exclude_schedule_id=schedule_id,
        prompt_ids=[p.root_prompt_id or p.id for p in prompts],
        model_ids=model_ids,
        persona_ids=persona_ids,
    )

    return render(
        request,
        "schedules/_occurrence_preview.html",
        {
            "incomplete": False,
            "upcoming": upcoming,
            "prompt_count": len(prompts),
            "model_count": len(model_ids),
            "persona_count": len(persona_ids),
            "run_count_per_window": len(prompts) * len(model_ids) * len(persona_ids),
            "total_occurrences": total_occurrences,
            "per_window_cost": per_window_cost,
            "total_cost": (
                per_window_cost * total_occurrences
                if per_window_cost is not None and total_occurrences is not None
                else None
            ),
            "cost_known_combos": known_combos,
            "cost_total_combos": total_combos,
            "overlaps": overlaps,
        },
    )


def _grouped_schedules_by_client(t, schedules: list[RunSchedule]) -> list[dict]:
    """[{client_id, client_name, schedules, summary}] from `all_schedules`'s pre-sorted-by-

    client-name list — `summary` is a pre-composed "N active, M paused" string (omitting a zero
    category rather than saying "0 paused"), built here so the template only has to iterate, not
    compose i18n strings inline with conditional Jinja logic.
    """
    groups = []
    for client_name, group_iter in groupby(schedules, key=lambda s: s.client.name):
        group = list(group_iter)
        active_count = sum(1 for s in group if s.is_active)
        paused_count = len(group) - active_count
        parts = []
        if active_count:
            parts.append(t("schedules.group_active_count").format(count=active_count))
        if paused_count:
            parts.append(t("schedules.group_paused_count").format(count=paused_count))
        groups.append(
            {
                "client_id": group[0].client_id,
                "client_name": client_name,
                "schedules": group,
                "summary": ", ".join(parts),
            }
        )
    return groups


def _health_strip_groups(db: Session, t) -> list[dict]:
    """[{client_id, client_name, has_problem, schedule_count, schedules}] for the "Historie"

    health strip (design decision 37) — one entry per client that has at least one schedule with
    terminal history, `schedules` being [{schedule, target_label, target_url, tiles, has_problem}].
    A client with no `error` tile anywhere in its schedules' last ~10 windows collapses by default
    in the template (`has_problem=False`); one with any error stays expanded so it's the first
    thing visible.
    """
    health = schedule_health(db, limit_per_schedule=_HEALTH_STRIP_WINDOW_COUNT)
    if not health:
        return []
    schedules = db.scalars(
        select(RunSchedule)
        .join(Client, RunSchedule.client_id == Client.id)
        .options(joinedload(RunSchedule.client))
        .where(RunSchedule.id.in_(health.keys()))
        .order_by(Client.name.asc())
    ).all()

    groups = []
    for client_name, group_iter in groupby(schedules, key=lambda s: s.client.name):
        group = list(group_iter)
        schedule_entries = []
        client_has_problem = False
        for schedule in group:
            tiles = health.get(schedule.id, [])
            has_problem = any(tile.outcome == "error" for tile in tiles)
            client_has_problem = client_has_problem or has_problem
            target_label, target_url, target_detail = schedule_target_display(db, schedule)
            subtitle = (
                t("schedules.target_subtitle_set").format(count=target_detail)
                if schedule.target_type == "prompt_set"
                else t("schedules.target_subtitle_prompt").format(set_name=target_detail)
            )
            schedule_entries.append(
                {
                    "schedule": schedule,
                    "target_label": target_label,
                    "target_url": target_url,
                    "subtitle": subtitle,
                    "tiles": tiles,
                    "has_problem": has_problem,
                }
            )
        groups.append(
            {
                "client_id": group[0].client_id,
                "client_name": client_name,
                "has_problem": client_has_problem,
                "schedule_count": len(group),
                "schedules": schedule_entries,
            }
        )
    return groups


@router.get("", dependencies=_editor_or_admin)
def schedules_monitor(
    request: Request,
    view: Literal["schedules", "queue", "history"] = Query("schedules", description="Which of the three monitoring views to show."),
    status: Literal["all", "errors", "skipped"] = Query(
        "all", description="History view only: keep only batches with at least one item in that status."
    ),
    q: str = Query("", description="History view only: free-text search across prompt text and client name."),
    page: int = Query(1, ge=1, description="History view only: 1-indexed page of batches."),
    db: Session = Depends(get_db),
):
    """Monitoring page (docs/TASKS_SCHEDULER.md T6): worker liveness, then one of three views —

    every schedule across every client, grouped by client with search/filter and its overlap
    badge (T6, extending SCH-5c's save-time warning to a persistent one); the active queue in
    claim order with a KPI summary, auto-refreshed client-side via HTMX polling; or searchable,
    batched history. Each view's data is computed only when it's the one being shown, not all
    three on every request.
    """
    t = get_t(request)
    now = datetime.now(timezone.utc)

    workers = [
        {
            "worker_name": worker_status.worker_name,
            "text": t("schedules.worker_stale" if worker_status.is_stale else "schedules.worker_running").format(
                duration=format_duration_short(t, worker_status.seconds_since)
            ),
            "is_stale": worker_status.is_stale,
            "dry_run": worker_status.dry_run,
        }
        for worker_status in worker_statuses(db, now=now)
    ]

    context = {"view": view, "status": status, "q": q, "page": page, "workers": workers}

    if view == "schedules":
        schedules = all_schedules(db)
        context.update(
            {
                "schedule_groups": _grouped_schedules_by_client(t, schedules),
                "schedule_summaries": {s.id: schedule_summary_text(t, s) for s in schedules},
                "schedule_targets": {s.id: schedule_target_display(db, s) for s in schedules},
                "overlap_counts": find_all_overlap_counts(db, schedules),
            }
        )
    elif view == "queue":
        oldest_age = oldest_queued_age_seconds(db, now=now)
        context.update(
            {
                "queue_rows": active_queue_rows(db),
                "queue_summary": queue_summary(db),
                "oldest_queued_duration": format_duration_short(t, oldest_age) if oldest_age is not None else None,
            }
        )
    else:
        batches, has_next = history_batches(db, status_filter=status, search=q, page=page, page_size=50)
        # The health strip is a landing-page overview, not another filtered result — once the
        # user is searching or paging, they're already investigating something specific, and
        # showing it would be redundant (design decision 37).
        show_health_strip = page == 1 and not q and status == "all"
        context.update(
            {
                "history_batches": batches,
                "history_has_next": has_next,
                "health_groups": _health_strip_groups(db, t) if show_health_strip else [],
                "health_window_count": _HEALTH_STRIP_WINDOW_COUNT,
            }
        )

    return render(request, "schedules/index.html", context)
