"""Client CRUD routes: list, create, view, edit (docs/REQUIREMENTS.md FR-1..FR-3).

Strategy/reputation fields are explicitly out of scope for phase 1 — see
the skill's "Build sequencing" section.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.config import get_settings
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Citation, Client, ClientAlias, Prompt, PromptSet, Provider, RawResponse, Run, TrackedEntity, TrackedEntityAlias, User
from app.models.verification import CitationVerification
from app.services.claim_judge import LLM_JUDGE_PROVIDERS, LLM_JUDGED_VERDICTS
from app.services.cost import average_llm_judge_cost_per_citation
from app.services.verification_queue import enqueue_judge
from app.templating import get_t, render
from app.utils import unique_slugify

# Reused on every create/edit/delete route below (docs/TASKS_PHASE6.md P6-T6) — viewer can read
# everything on this router, but not change anything.
_editor_or_admin = [Depends(require_role("admin", "editor"))]

# Narrower than _editor_or_admin, and used by exactly one route below (docs/TASKS_PRE_SCHEDULER.md
# design decision 14): marking a client as a test client rewrites every figure on /ops for that
# client's entire history, which is an admin decision even though ordinary client editing is not.
_admin_only = [Depends(require_role("admin"))]

router = APIRouter(prefix="/clients", tags=["clients"])


def _get_client_or_404(db: Session, request: Request, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise AppError("client_not_found", get_t(request)("errors.client_not_found"), status_code=404)
    return client


def _get_client_alias_or_404(db: Session, request: Request, client_id: int, alias_id: int) -> ClientAlias:
    alias = db.scalar(
        select(ClientAlias).where(ClientAlias.id == alias_id, ClientAlias.client_id == client_id)
    )
    if alias is None:
        raise AppError("client_alias_not_found", get_t(request)("errors.client_alias_not_found"), status_code=404)
    return alias


def _get_tracked_entity_or_404(db: Session, request: Request, client_id: int, entity_id: int) -> TrackedEntity:
    entity = db.scalar(
        select(TrackedEntity).where(TrackedEntity.id == entity_id, TrackedEntity.client_id == client_id)
    )
    if entity is None:
        raise AppError(
            "tracked_entity_not_found", get_t(request)("errors.tracked_entity_not_found"), status_code=404
        )
    return entity


def _get_tracked_entity_alias_or_404(
    db: Session, request: Request, client_id: int, entity_id: int, alias_id: int
) -> TrackedEntityAlias:
    alias = db.scalar(
        select(TrackedEntityAlias)
        .join(TrackedEntity, TrackedEntityAlias.tracked_entity_id == TrackedEntity.id)
        .where(
            TrackedEntityAlias.id == alias_id,
            TrackedEntityAlias.tracked_entity_id == entity_id,
            TrackedEntity.client_id == client_id,
        )
    )
    if alias is None:
        raise AppError(
            "tracked_entity_not_found", get_t(request)("errors.tracked_entity_not_found"), status_code=404
        )
    return alias


def _prompt_sets_for_client(db: Session, client_id: int) -> list[PromptSet]:
    """Prompt sets for one client, newest first — shared by every route rendering clients/detail.html."""
    return list(
        db.scalars(select(PromptSet).where(PromptSet.client_id == client_id).order_by(PromptSet.created_at.desc()))
    )


def _client_detail_conflict_response(request: Request, db: Session, client: Client, error_message: str):
    """Re-render the client detail page with a 409 error banner — the shared shape for every
    conflict on this page (a duplicate alias/tracked entity, or a blocked delete), so each route
    doesn't redefine the same render() call with only the error message differing.
    """
    return render(
        request,
        "clients/detail.html",
        {
            "client": client,
            "prompt_sets": _prompt_sets_for_client(db, client.id),
            "error": error_message,
        },
        status_code=409,
    )


def _client_run_count(db: Session, client_id: int) -> int:
    """How many runs exist under any prompt set/prompt of this client — the delete-block check."""
    return (
        db.scalar(
            select(func.count(Run.id))
            .join(Prompt, Run.prompt_id == Prompt.id)
            .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
            .where(PromptSet.client_id == client_id)
        )
        or 0
    )


def _parse_verify_date_range(t, date_from: str, date_to: str) -> tuple[datetime, datetime]:
    """`(date_from, date_to)` form strings ("YYYY-MM-DD") into an inclusive UTC datetime range —

    docs/TASKS_CITATION_VERIFICATION.md T13's retroactive bulk-verify. `date_to` is widened to
    the END of that day (23:59:59.999999), not its midnight start, so a run that happened at
    14:00 on the last day of the range is actually included, not silently excluded by an
    off-by-one against a `<=` comparison against midnight.
    """
    try:
        start = datetime.strptime(date_from.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(date_to.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(microseconds=1)
    except ValueError:
        raise AppError("invalid_date_range", t("errors.invalid_date_range"), status_code=400) from None
    if start > end:
        raise AppError("invalid_date_range", t("errors.invalid_date_range"), status_code=400)
    return start, end


def _bulk_verify_candidate_raw_response_ids(db: Session, client_id: int, start: datetime, end: datetime) -> list[int]:
    """Raw responses eligible for a retroactive bulk LLM-judge run (docs/TASKS_CITATION_

    VERIFICATION.md T13): this client's, in `[start, end]`, from a provider `judge_citations` can
    do anything with (design decision 4 — Anthropic/Perplexity already get the free quote check,
    T8; xAI/DeepSeek have no claim to judge), with at least one citation, and NOT already judged —
    a response with a REAL LLM verdict (`LLM_JUDGED_VERDICTS`) is excluded so re-running this
    doesn't pay to re-judge citations that already have one (a human can still re-check one
    citation at a time via the run detail page's own button if they specifically want a fresh
    judgement). A `check_type='llm'` row whose own verdict is `unverifiable` because its source
    failed to CAPTURE (`reason` set, e.g. `robots`/`http_403`) does NOT count as already judged
    (code-review finding, 2026-09-30, same gap `app/routers/verification.py`'s `_citation_strata`
    already excludes for blind-labeling) — that's a free placeholder `judge_citations` writes with
    no LLM call made, so a response stuck with one must stay eligible once its source is captured
    successfully.

    A `check_type='llm'` row with `verdict='unverifiable'` and `reason IS NULL`, by contrast, DOES
    count as already judged (code-review finding, 2026-09-30, round 2) — `judge_citations` writes
    that shape only when the LLM was actually called (real, billed `cost_usd`/tokens) but its reply
    couldn't be parsed into a recognized verdict. Treating that the same as a free capture-failure
    placeholder would let a client whose judge model consistently returns malformed output for one
    citation get re-billed for it on every future bulk-verify run, forever, with no way to converge.
    """
    already_judged = (
        select(Citation.raw_response_id)
        .join(CitationVerification, CitationVerification.citation_id == Citation.id)
        .where(
            CitationVerification.check_type == "llm",
            or_(
                CitationVerification.verdict.in_(LLM_JUDGED_VERDICTS),
                and_(CitationVerification.verdict == "unverifiable", CitationVerification.reason.is_(None)),
            ),
        )
    )
    query = (
        select(RawResponse.id)
        .join(Run, RawResponse.run_id == Run.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .join(Provider, AIModel.provider_id == Provider.id)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .where(
            PromptSet.client_id == client_id,
            Provider.code.in_(LLM_JUDGE_PROVIDERS),
            RawResponse.has_citations.is_(True),
            Run.started_at >= start,
            Run.started_at <= end,
            RawResponse.id.not_in(already_judged),
        )
    )
    return list(db.scalars(query).all())


def _citation_count(db: Session, raw_response_ids: list[int]) -> int:
    if not raw_response_ids:
        return 0
    return db.scalar(select(func.count(Citation.id)).where(Citation.raw_response_id.in_(raw_response_ids))) or 0


@router.get("")
def list_clients(request: Request, db: Session = Depends(get_db)):
    """List all clients, newest first (FR-2)."""
    clients = db.scalars(select(Client).order_by(Client.created_at.desc())).all()
    return render(request, "clients/list.html", {"clients": clients})


@router.get("/new", dependencies=_editor_or_admin)
def new_client_form(request: Request):
    """Render the empty client-creation form."""
    t = get_t(request)
    return render(
        request,
        "clients/form.html",
        {"title": t("client.create_title"), "action": "/clients", "cancel_url": "/clients", "client": None},
    )


@router.post("", dependencies=_editor_or_admin)
def create_client(
    name: str = Form(..., description="Client's display name."),
    industry: str = Form("", description="Free-text industry label, e.g. 'Automotive'."),
    notes: str = Form("", description="Free-text notes about this client."),
    domain: str = Form(
        "",
        description="Client's own primary domain, e.g. 'acme.com' — used to detect when the client's "
        "own site is among a run's cited sources.",
    ),
    db: Session = Depends(get_db),
):
    """Create a new client with name, industry, notes, and domain (FR-1).

    A URL-safe slug is auto-derived from the name; it is not user-editable
    and never changes after creation.
    """
    slug = unique_slugify(db, Client, name)
    client = Client(
        name=name.strip(),
        slug=slug,
        industry=industry.strip() or None,
        notes=notes.strip() or None,
        domain=domain.strip().lower() or None,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return RedirectResponse(url=f"/clients/{client.id}", status_code=303)


@router.get("/{client_id}")
def client_detail(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Show one client's details, its aliases, and its prompt sets."""
    # Deferred: app/routers/schedules.py imports from app/routers/prompts.py, so a top-level
    # import here (clients -> schedules -> prompts) risks the same cycle app/errors.py's
    # deferred `app.templating` import already documents a precedent for.
    from app.routers.schedules import schedule_summary_text, schedule_target_display, schedules_for_client

    client = _get_client_or_404(db, request, client_id)
    prompt_sets = _prompt_sets_for_client(db, client_id)
    t = get_t(request)
    schedules = schedules_for_client(db, client_id)
    return render(
        request,
        "clients/detail.html",
        {
            "client": client,
            "prompt_sets": prompt_sets,
            "schedules": schedules,
            "schedule_summaries": {s.id: schedule_summary_text(t, s) for s in schedules},
            "schedule_targets": {s.id: schedule_target_display(db, s) for s in schedules},
            "show_prompt_column": True,
            "new_schedule_url": None,
        },
    )


@router.get("/{client_id}/edit", dependencies=_editor_or_admin)
def edit_client_form(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Render the client edit form, pre-filled with current values (FR-3, T10)."""
    client = _get_client_or_404(db, request, client_id)
    t = get_t(request)
    return render(
        request,
        "clients/form.html",
        {
            "title": t("client.edit_title"),
            "action": f"/clients/{client_id}/edit",
            "cancel_url": f"/clients/{client_id}",
            "client": client,
            "default_daily_run_limit": get_settings().scheduler_default_daily_run_limit,
        },
    )


@router.post("/{client_id}/edit", dependencies=_editor_or_admin)
def update_client(
    request: Request,
    client_id: int,
    name: str = Form(..., description="Client's display name."),
    industry: str = Form("", description="Free-text industry label."),
    notes: str = Form("", description="Free-text notes about this client."),
    domain: str = Form(
        "",
        description="Client's own primary domain, e.g. 'acme.com' — used to detect when the client's "
        "own site is among a run's cited sources.",
    ),
    priority: int = Form(100, description="Scheduler priority weight — higher runs first when the queue is contested."),
    daily_run_limit: str = Form(
        "", description="Hard cap on runs per rolling 24h for this client. Empty uses the app-wide default."
    ),
    monthly_budget_usd: str = Form(
        "", description="Soft monthly spend threshold in USD — crossing it only sends a notification. Empty means no threshold."
    ),
    db: Session = Depends(get_db),
):
    """Update an existing client's name, industry, notes, domain, and scheduler settings (FR-3,

    docs/TASKS_SCHEDULER.md T10). The slug is immutable.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    client.name = name.strip()
    client.industry = industry.strip() or None
    client.notes = notes.strip() or None
    client.domain = domain.strip().lower() or None
    # Bounds found in code review, 2026-09-22: `priority` feeds `client.priority * 1000 +
    # schedule.priority` (app/services/queue.py) on every enqueue, stored into run_queue's own
    # `integer` column — an unbounded value here could overflow that column and crash the next
    # enqueue pass with a 500 instead of a friendly validation error at the one place it was
    # actually typed in. 10000 leaves ample room for that multiplication to never approach the
    # ~2.1 billion `integer` ceiling even with a large schedule-level priority on top.
    if not (1 <= priority <= 10000):
        raise AppError("invalid_priority", t("errors.invalid_priority"), status_code=400)
    client.priority = priority
    if daily_run_limit.strip():
        try:
            parsed_daily_run_limit = int(daily_run_limit.strip())
        except ValueError:
            raise AppError("invalid_daily_run_limit", t("errors.invalid_daily_run_limit"), status_code=400) from None
        if not (0 <= parsed_daily_run_limit <= 100000):
            raise AppError("invalid_daily_run_limit", t("errors.invalid_daily_run_limit"), status_code=400)
        client.daily_run_limit = parsed_daily_run_limit
    else:
        client.daily_run_limit = None
    if monthly_budget_usd.strip():
        try:
            parsed_monthly_budget = Decimal(monthly_budget_usd.strip())
        except InvalidOperation:
            raise AppError("invalid_monthly_budget", t("errors.invalid_monthly_budget"), status_code=400) from None
        # `Decimal` parses "Infinity"/"NaN" without raising (found in code review, 2026-09-22) —
        # "Infinity" would silently defeat check_budget_thresholds's own "spend < budget" test
        # forever (never warns again), and "NaN" makes that same comparison False every time,
        # firing on every check instead of once a month. Neither is a number a real budget can be.
        # The upper bound is a business ceiling, not the column's Numeric(10, 2) capacity (revised
        # 2026-09-22) — realistic per-client spend is tens to low hundreds of dollars a month, so
        # $100,000 is already a 1000x+ margin above anything legitimate while still catching an
        # obvious typo (an extra digit, cents entered as dollars) far sooner than the column's own
        # ~$100M ceiling would.
        if not parsed_monthly_budget.is_finite() or not (0 <= parsed_monthly_budget < 100_000):
            raise AppError("invalid_monthly_budget", t("errors.invalid_monthly_budget"), status_code=400)
        client.monthly_budget_usd = parsed_monthly_budget
    else:
        client.monthly_budget_usd = None
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/toggle-test", dependencies=_admin_only)
def toggle_client_test(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Flip a client's `is_test` flag — whether its runs count into the `/ops` aggregates.

    Admin-only, and deliberately its own action rather than a field on the client form: an
    unchecked HTML checkbox is not submitted at all, so a hidden-from-editors field on the shared
    form would silently reset the flag to false the first time an editor saved that client
    (docs/TASKS_PRE_SCHEDULER.md design decision 14). A separate route removes that class of bug
    instead of guarding against it.

    Takes effect on the client's WHOLE history, not just runs from now on: the flag is applied as a
    filter when each ops query runs and is never written onto a run, so turning it on drops every
    past run of this client out of the `/ops` totals and turning it off brings them all back
    (design decision 15). Never touches evidence — no run, raw response or citation is modified,
    and the client stays fully visible in `/clients` and on `/dashboard` either way.
    """
    client = _get_client_or_404(db, request, client_id)
    client.is_test = not client.is_test
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/toggle-auto-verify-citations", dependencies=_editor_or_admin)
def toggle_client_auto_verify_citations(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Flip a client's `auto_verify_citations` flag (docs/TASKS_CITATION_VERIFICATION.md T13,

    design decision 25) — whether a NEW run's OpenAI/Gemini citations get the paid LLM paraphrase
    check automatically, right after their sources are captured.

    Editor-or-admin, not admin-only like `toggle_client_test` above: unlike that flag, this one
    doesn't retroactively rewrite any historical `/ops` figures — it only affects runs from now
    on, and editors already manage every other operational client setting via the main form.
    Its own route rather than a checkbox on that form for the same reason `is_test` has one: an
    unchecked HTML checkbox is never submitted at all, so a field hidden from nobody but still
    easy to overlook could get silently reset the next time the client is saved for an unrelated
    edit (docs/TASKS_PRE_SCHEDULER.md design decision 14).
    """
    client = _get_client_or_404(db, request, client_id)
    client.auto_verify_citations = not client.auto_verify_citations
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/verify-retroactively/preview", dependencies=_editor_or_admin)
def verify_retroactively_preview(
    request: Request,
    client_id: int,
    date_from: str = Form(..., description="Start of the date range (YYYY-MM-DD), inclusive."),
    date_to: str = Form(..., description="End of the date range (YYYY-MM-DD), inclusive."),
    db: Session = Depends(get_db),
):
    """Preview a retroactive bulk LLM-judge run over `[date_from, date_to]` (docs/TASKS_CITATION_

    VERIFICATION.md T13 point 3) — count of eligible responses/citations and an estimated cost,
    written nowhere: only the separate confirm step below actually enqueues anything. The estimate
    is the average of past real `check_type='llm'` verification costs times the citation count in
    range (`app.services.cost.average_llm_judge_cost_per_citation`) — shown as "unknown" rather
    than a fabricated number when no LLM judgement has ever run yet to average.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    start, end = _parse_verify_date_range(t, date_from, date_to)

    raw_response_ids = _bulk_verify_candidate_raw_response_ids(db, client_id, start, end)
    citation_count = _citation_count(db, raw_response_ids)
    average_cost = average_llm_judge_cost_per_citation(db)

    return render(
        request,
        "clients/verify_retroactively_preview.html",
        {
            "client": client,
            "date_from": date_from,
            "date_to": date_to,
            "raw_response_count": len(raw_response_ids),
            "citation_count": citation_count,
            "estimated_cost_usd": average_cost * citation_count if average_cost is not None else None,
        },
    )


@router.post("/{client_id}/verify-retroactively/confirm", dependencies=_editor_or_admin)
def verify_retroactively_confirm(
    request: Request,
    client_id: int,
    date_from: str = Form(..., description="Start of the date range (YYYY-MM-DD), inclusive — re-validated, never trusted from the preview page."),
    date_to: str = Form(..., description="End of the date range (YYYY-MM-DD), inclusive — re-validated, never trusted from the preview page."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Enqueue a 'judge' `VerificationJob` for every response the SAME query the preview used

    still finds eligible (docs/TASKS_CITATION_VERIFICATION.md T13 point 3) — the date range is
    re-parsed and re-queried against the database here, never trusting the preview page's own
    displayed counts, the same discipline app/routers/prompt_sets.py's bulk-import confirm step
    already follows for its preview (re-resolves everything itself rather than trusting the
    round-tripped form). Something judged in the meantime, or a response that stopped being
    eligible, is simply not counted again — this can only enqueue fewer jobs than the preview
    showed, never more.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    start, end = _parse_verify_date_range(t, date_from, date_to)

    raw_response_ids = _bulk_verify_candidate_raw_response_ids(db, client_id, start, end)
    now = datetime.now(timezone.utc)
    for raw_response_id in raw_response_ids:
        enqueue_judge(db, raw_response_id, now=now, requested_by_user_id=user.id)
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/delete", dependencies=_editor_or_admin)
def delete_client(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Delete a client and everything under it, unless any of its runs would be lost.

    Blocked (inline error, not a raw API error) if any prompt set/prompt
    belonging to this client has a recorded run — deleting evidence is
    never allowed (NFR-6). Otherwise the database cascade removes the
    client's (necessarily run-less) prompt sets and prompts along with it.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    run_count = _client_run_count(db, client_id)
    if run_count:
        return _client_detail_conflict_response(request, db, client, t("errors.client_in_use").format(count=run_count))
    db.delete(client)
    db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/{client_id}/aliases", dependencies=_editor_or_admin)
def create_client_alias(
    request: Request,
    client_id: int,
    alias: str = Form(
        ..., max_length=200, description="Alternate name/spelling to match against, e.g. 'Acme Corp'."
    ),
    db: Session = Depends(get_db),
):
    """Add an alternate name/spelling for a client, used by the mention_visibility analysis skill
    alongside the client's own name when matching a run's rendered text.

    Duplicate detection is case-insensitive (the matching engine itself is
    case-insensitive, so 'Acme' and 'acme' are the same alias for its
    purposes) and enforced at both layers: a pre-check here for a fast,
    specific 409 in the common case, and a DB-level functional unique index
    on (client_id, lower(alias)) (migration 0013) as the actual source of
    truth — a concurrent duplicate submission that races past the pre-check
    still hits that constraint, caught below and turned into the same 409
    instead of an unhandled IntegrityError.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    alias = alias.strip()

    existing = db.scalar(
        select(ClientAlias).where(ClientAlias.client_id == client_id, func.lower(ClientAlias.alias) == alias.lower())
    )
    if existing is not None:
        return _client_detail_conflict_response(request, db, client, t("errors.client_alias_duplicate"))
    db.add(ClientAlias(client_id=client_id, alias=alias))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _client_detail_conflict_response(request, db, client, t("errors.client_alias_duplicate"))
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/aliases/{alias_id}/delete", dependencies=_editor_or_admin)
def delete_client_alias(request: Request, client_id: int, alias_id: int, db: Session = Depends(get_db)):
    """Delete an alias. Aliases are configuration, not evidence — no in-use check, always allowed."""
    alias = _get_client_alias_or_404(db, request, client_id, alias_id)
    db.delete(alias)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/tracked-entities", dependencies=_editor_or_admin)
def create_tracked_entity(
    request: Request,
    client_id: int,
    name: str = Form(..., max_length=200, description="Competitor's display name, e.g. 'Volkswagen'."),
    domain: str = Form(
        "",
        description="Competitor's own domain, e.g. 'vw.com' — used for citation matching, same role as the "
        "client's own domain.",
    ),
    db: Session = Depends(get_db),
):
    """Add a competitor to track alongside this client, for the competitive_visibility analysis skill
    (docs/TASKS_PHASE5.md P5-T4).

    Duplicate detection mirrors client aliases: a case-insensitive pre-check here for a fast 409, and
    the DB-level functional unique index (migration 0017) as the actual source of truth for a
    concurrent duplicate that races past the pre-check.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    name = name.strip()

    existing = db.scalar(
        select(TrackedEntity).where(
            TrackedEntity.client_id == client_id, func.lower(TrackedEntity.name) == name.lower()
        )
    )
    if existing is not None:
        return _client_detail_conflict_response(request, db, client, t("errors.tracked_entity_duplicate"))
    db.add(TrackedEntity(client_id=client_id, name=name, domain=domain.strip().lower() or None))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _client_detail_conflict_response(request, db, client, t("errors.tracked_entity_duplicate"))
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/tracked-entities/{entity_id}/delete", dependencies=_editor_or_admin)
def delete_tracked_entity(request: Request, client_id: int, entity_id: int, db: Session = Depends(get_db)):
    """Delete a tracked entity and its aliases. Configuration, not evidence — no in-use check, always allowed."""
    entity = _get_tracked_entity_or_404(db, request, client_id, entity_id)
    db.delete(entity)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/tracked-entities/{entity_id}/aliases", dependencies=_editor_or_admin)
def create_tracked_entity_alias(
    request: Request,
    client_id: int,
    entity_id: int,
    alias: str = Form(
        ..., max_length=200, description="Alternate name/spelling to match against, e.g. 'VW'."
    ),
    db: Session = Depends(get_db),
):
    """Add an alternate name/spelling for a tracked entity — mirrors client alias handling one level
    down (docs/TASKS_PHASE5.md P5-T4).
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    entity = _get_tracked_entity_or_404(db, request, client_id, entity_id)
    alias = alias.strip()

    existing = db.scalar(
        select(TrackedEntityAlias).where(
            TrackedEntityAlias.tracked_entity_id == entity.id, func.lower(TrackedEntityAlias.alias) == alias.lower()
        )
    )
    if existing is not None:
        return _client_detail_conflict_response(request, db, client, t("errors.tracked_entity_duplicate"))
    db.add(TrackedEntityAlias(tracked_entity_id=entity.id, alias=alias))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _client_detail_conflict_response(request, db, client, t("errors.tracked_entity_duplicate"))
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/tracked-entities/{entity_id}/aliases/{alias_id}/delete", dependencies=_editor_or_admin)
def delete_tracked_entity_alias(
    request: Request, client_id: int, entity_id: int, alias_id: int, db: Session = Depends(get_db)
):
    """Delete a tracked entity's alias. Configuration, not evidence — always allowed."""
    alias = _get_tracked_entity_alias_or_404(db, request, client_id, entity_id, alias_id)
    db.delete(alias)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)
