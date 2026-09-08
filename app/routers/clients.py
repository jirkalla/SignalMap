"""Client CRUD routes: list, create, view, edit (docs/REQUIREMENTS.md FR-1..FR-3).

Strategy/reputation fields are explicitly out of scope for phase 1 — see
the skill's "Build sequencing" section.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Client, PromptSet
from app.templating import get_t, render
from app.utils import unique_slugify

router = APIRouter(prefix="/clients", tags=["clients"])


def _get_client_or_404(db: Session, request: Request, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise AppError("client_not_found", get_t(request)("errors.client_not_found"), status_code=404)
    return client


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
    db: Session = Depends(get_db),
):
    """Create a new client with name, industry, and notes (FR-1).

    A URL-safe slug is auto-derived from the name; it is not user-editable
    and never changes after creation.
    """
    slug = unique_slugify(db, Client, name)
    client = Client(name=name.strip(), slug=slug, industry=industry.strip() or None, notes=notes.strip() or None)
    db.add(client)
    db.commit()
    db.refresh(client)
    return RedirectResponse(url=f"/clients/{client.id}", status_code=303)


@router.get("/{client_id}")
def client_detail(request: Request, client_id: int, db: Session = Depends(get_db)):
    """Show one client's details plus its prompt sets."""
    client = _get_client_or_404(db, request, client_id)
    prompt_sets = db.scalars(
        select(PromptSet).where(PromptSet.client_id == client_id).order_by(PromptSet.created_at.desc())
    ).all()
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
    db: Session = Depends(get_db),
):
    """Update an existing client's name, industry, and notes (FR-3). The slug is immutable."""
    client = _get_client_or_404(db, request, client_id)
    client.name = name.strip()
    client.industry = industry.strip() or None
    client.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)
