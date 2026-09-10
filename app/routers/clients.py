"""Client CRUD routes: list, create, view, edit (docs/REQUIREMENTS.md FR-1..FR-3).

Strategy/reputation fields are explicitly out of scope for phase 1 — see
the skill's "Build sequencing" section.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Client, ClientAlias, Prompt, PromptSet, Run
from app.templating import get_t, render
from app.utils import unique_slugify

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


def _prompt_sets_for_client(db: Session, client_id: int) -> list[PromptSet]:
    """Prompt sets for one client, newest first — shared by every route rendering clients/detail.html."""
    return list(
        db.scalars(select(PromptSet).where(PromptSet.client_id == client_id).order_by(PromptSet.created_at.desc()))
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


@router.get("/new")
def new_client_form(request: Request):
    """Render the empty client-creation form."""
    t = get_t(request)
    return render(
        request,
        "clients/form.html",
        {"title": t("client.create_title"), "action": "/clients", "cancel_url": "/clients", "client": None},
    )


@router.post("")
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


@router.get("/{client_id}/edit")
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


@router.post("/{client_id}/edit")
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


@router.post("/{client_id}/delete")
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
        return render(
            request,
            "clients/detail.html",
            {
                "client": client,
                "prompt_sets": _prompt_sets_for_client(db, client_id),
                "error": t("errors.client_in_use").format(count=run_count),
            },
            status_code=409,
        )
    db.delete(client)
    db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/{client_id}/aliases")
def create_client_alias(
    request: Request,
    client_id: int,
    alias: str = Form(..., description="Alternate name/spelling to match against, e.g. 'Acme Corp'."),
    db: Session = Depends(get_db),
):
    """Add an alternate name/spelling for a client, used by the mention_visibility analysis skill
    alongside the client's own name when matching a run's rendered text.
    """
    t = get_t(request)
    client = _get_client_or_404(db, request, client_id)
    alias = alias.strip()
    # Case-insensitive on purpose: the matching engine itself is case-insensitive
    # (re.IGNORECASE), so 'Acme' and 'acme' are the same alias for its purposes —
    # storing both would just clutter matched_terms without changing any result.
    existing = db.scalar(
        select(ClientAlias).where(ClientAlias.client_id == client_id, func.lower(ClientAlias.alias) == alias.lower())
    )
    if existing is not None:
        return render(
            request,
            "clients/detail.html",
            {
                "client": client,
                "prompt_sets": _prompt_sets_for_client(db, client_id),
                "error": t("errors.client_alias_duplicate"),
            },
            status_code=409,
        )
    db.add(ClientAlias(client_id=client_id, alias=alias))
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/aliases/{alias_id}/delete")
def delete_client_alias(request: Request, client_id: int, alias_id: int, db: Session = Depends(get_db)):
    """Delete an alias. Aliases are configuration, not evidence — no in-use check, always allowed."""
    alias = _get_client_alias_or_404(db, request, client_id, alias_id)
    db.delete(alias)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)
