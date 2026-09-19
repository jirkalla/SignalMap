"""Client CRUD routes: list, create, view, edit (docs/REQUIREMENTS.md FR-1..FR-3).

Strategy/reputation fields are explicitly out of scope for phase 1 — see
the skill's "Build sequencing" section.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.errors import AppError
from app.models import Client, ClientAlias, Prompt, PromptSet, Run, TrackedEntity, TrackedEntityAlias
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
    client = _get_client_or_404(db, request, client_id)
    prompt_sets = _prompt_sets_for_client(db, client_id)
    return render(request, "clients/detail.html", {"client": client, "prompt_sets": prompt_sets})


@router.get("/{client_id}/edit", dependencies=_editor_or_admin)
def edit_client_form(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Render the client edit form, pre-filled with current values (FR-3)."""
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
    db: Session = Depends(get_db),
):
    """Update an existing client's name, industry, notes, and domain (FR-3). The slug is immutable."""
    client = _get_client_or_404(db, request, client_id)
    client.name = name.strip()
    client.industry = industry.strip() or None
    client.notes = notes.strip() or None
    client.domain = domain.strip().lower() or None
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
