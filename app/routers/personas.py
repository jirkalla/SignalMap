"""Persona CRUD: who a run's system_instruction can frame the asker as (app/models/persona.py).

Personas are pure data, same as Market — full CRUD is safe here. Exactly one row may be the
default (partial unique index, migration 0021); this router enforces that by always clearing
the previous default before setting a new one, in the same transaction, whenever a submit would
otherwise leave two rows (or zero rows) with `is_default = True`.

Delete is blocked both when a persona is the current default, and (since `runs.persona_id`,
migration 0022) when any `Run` references it — `runs.persona_id`'s foreign key has no `ON
DELETE` behavior (unlike e.g. `citations.raw_response_id`'s CASCADE), so without this app-level
check a delete attempt against an in-use persona would surface as a raw, unhandled
IntegrityError instead of a clean structured error, same evidence-retention policy as
`markets`/`ai_models` delete.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.errors import AppError
from app.models import Persona, Run
from app.templating import get_t, render

router = APIRouter(prefix="/personas", tags=["personas"])

_editor_or_admin = [Depends(require_role("admin", "editor"))]


def _get_persona_or_404(db: Session, request: Request, persona_id: int) -> Persona:
    persona = db.get(Persona, persona_id)
    if persona is None:
        raise AppError("persona_not_found", get_t(request)("errors.persona_not_found"), status_code=404)
    return persona


def _persona_rows(db: Session) -> list[Persona]:
    return db.scalars(select(Persona).order_by(Persona.label)).all()


def _persona_run_count(db: Session, persona_id: int) -> int:
    """How many runs exist against this one persona — the delete-block check (same pattern as

    app/routers/markets.py's in-use check for Prompt.market_id).
    """
    return db.scalar(select(func.count(Run.id)).where(Run.persona_id == persona_id)) or 0


def _clear_current_default(db: Session, except_id: int | None) -> None:
    """Unset `is_default` on whichever persona currently has it (if any, and if it isn't
    `except_id` itself) — always called before setting a new default, in the same
    flush/transaction, so the partial unique index (`idx_personas_one_default`) never has to
    reject a legitimate write.
    """
    current_default = db.scalar(select(Persona).where(Persona.is_default.is_(True)))
    if current_default is not None and current_default.id != except_id:
        current_default.is_default = False
        db.flush()


@router.get("")
def list_personas(request: Request, db: Session = Depends(get_db)):
    """List every persona a run can be framed as, with which one is currently the default."""
    return render(request, "personas/list.html", {"rows": _persona_rows(db)})


@router.get("/new", dependencies=_editor_or_admin)
def new_persona_form(request: Request):
    """Render the empty persona-creation form."""
    t = get_t(request)
    return render(
        request,
        "personas/form.html",
        {"title": t("persona.create_title"), "action": "/personas", "cancel_url": "/personas", "persona": None},
    )


@router.post("", dependencies=_editor_or_admin)
def create_persona(
    request: Request,
    label: str = Form(
        ..., description="Free text substituted directly into the system_instruction template, e.g. 'manager'."
    ),
    is_default: bool = Form(
        False, description="Pre-selected persona for a new run's trigger form. Exactly one persona is ever the default."
    ),
    db: Session = Depends(get_db),
):
    """Create a new persona. Rejects a blank or duplicate label with an inline error, not a raw
    API error. Setting `is_default` here clears it from whichever persona currently has it.
    """
    t = get_t(request)
    label = label.strip()
    form_state = {"label": label, "is_default": is_default}

    error = None
    if not label:
        error = t("errors.persona_label_required")
    elif db.scalar(select(Persona).where(Persona.label == label)) is not None:
        error = t("errors.persona_label_conflict")

    if error:
        return render(
            request,
            "personas/form.html",
            {
                "title": t("persona.create_title"),
                "action": "/personas",
                "cancel_url": "/personas",
                "persona": form_state,
                "error": error,
            },
            status_code=409,
        )

    if is_default:
        _clear_current_default(db, except_id=None)

    persona = Persona(label=label, is_default=is_default)
    db.add(persona)
    db.commit()
    return RedirectResponse(url="/personas", status_code=303)


@router.get("/{persona_id}/edit", dependencies=_editor_or_admin)
def edit_persona_form(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Render the persona edit form, pre-filled with current values."""
    persona = _get_persona_or_404(db, request, persona_id)
    t = get_t(request)
    return render(
        request,
        "personas/form.html",
        {
            "title": t("persona.edit_title"),
            "action": f"/personas/{persona_id}/edit",
            "cancel_url": "/personas",
            "persona": persona,
        },
    )


@router.post("/{persona_id}/edit", dependencies=_editor_or_admin)
def update_persona(
    request: Request,
    persona_id: int,
    label: str = Form(..., description="Free text substituted directly into the system_instruction template."),
    is_default: bool = Form(False, description="Pre-selected persona for a new run's trigger form."),
    db: Session = Depends(get_db),
):
    """Update a persona.

    Checking `is_default` here (when this persona isn't already the default) clears it from
    whichever persona currently has it first, in the same transaction, so the partial unique
    index is never violated. Unchecking `is_default` on the persona that currently *is* the
    default is rejected with the same "set another default first" error `delete_persona` uses
    below — the table must never end up with zero default personas.
    """
    t = get_t(request)
    persona = _get_persona_or_404(db, request, persona_id)
    label = label.strip()
    form_state = {"id": persona_id, "label": label, "is_default": is_default}

    error = None
    if not label:
        error = t("errors.persona_label_required")
    elif persona.is_default and not is_default:
        error = t("errors.persona_is_default")
    else:
        conflict = db.scalar(select(Persona).where(Persona.label == label, Persona.id != persona_id))
        if conflict is not None:
            error = t("errors.persona_label_conflict")

    if error:
        return render(
            request,
            "personas/form.html",
            {
                "title": t("persona.edit_title"),
                "action": f"/personas/{persona_id}/edit",
                "cancel_url": "/personas",
                "persona": form_state,
                "error": error,
            },
            status_code=409,
        )

    if is_default and not persona.is_default:
        _clear_current_default(db, except_id=persona_id)

    persona.label = label
    persona.is_default = is_default
    db.commit()
    return RedirectResponse(url="/personas", status_code=303)


@router.post("/{persona_id}/delete", dependencies=_editor_or_admin)
def delete_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Delete a persona — blocked (inline error, not a raw API error) if it is the current
    default (the table must never end up with zero default personas), or if any `Run`
    references it (evidence retention — a run's recorded persona must never become dangling).
    """
    t = get_t(request)
    persona = _get_persona_or_404(db, request, persona_id)
    if persona.is_default:
        return render(
            request,
            "personas/list.html",
            {"rows": _persona_rows(db), "error": t("errors.persona_is_default")},
            status_code=409,
        )
    run_count = _persona_run_count(db, persona_id)
    if run_count:
        return render(
            request,
            "personas/list.html",
            {"rows": _persona_rows(db), "error": t("errors.persona_in_use").format(count=run_count)},
            status_code=409,
        )
    db.delete(persona)
    db.commit()
    return RedirectResponse(url="/personas", status_code=303)
