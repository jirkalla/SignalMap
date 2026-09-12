"""Provider admin: read/edit AI provider surfaces (app/models/provider.py).

Read + edit-name only, deliberately — adding a provider from the UI would
create a row with no adapter behind it (app/adapters/__init__.py's ADAPTERS
registry), which would just fail confusingly the moment someone tried to run
against it. A new provider is still a new adapter file plus a seed migration
(see docs/TASKS_PHASE2.md design decision 2). Model management (creating,
pricing, activation) lives in app/routers/ai_models.py.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Provider
from app.templating import get_t, render

# Admin-only end to end (docs/TASKS_PHASE6.md P6-T6) — even read access, unlike the
# create/edit/delete-only gating on clients/prompts/runs, since provider configuration isn't
# something an editor/viewer needs to see, let alone change.
router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(require_role("admin"))])


def _provider_rows(db: Session) -> list[tuple[Provider, int]]:
    """Every provider with its count of currently active models."""
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    active_counts = dict(
        db.execute(
            select(AIModel.provider_id, func.count(AIModel.id))
            .where(AIModel.is_active.is_(True))
            .group_by(AIModel.provider_id)
        ).all()
    )
    return [(p, active_counts.get(p.id, 0)) for p in providers]


@router.get("")
def list_providers(request: Request, db: Session = Depends(get_db)):
    """List every AI provider with an inline edit form for its display name.

    Read + edit-name only — see module docstring for why create/delete
    aren't offered here.
    """
    return render(request, "providers/list.html", {"rows": _provider_rows(db)})


@router.post("/{provider_id}")
def update_provider(
    request: Request,
    provider_id: int,
    name: str = Form(..., description="Display name shown across the app, e.g. 'Anthropic Claude'."),
    db: Session = Depends(get_db),
):
    """Update a provider's display name. `code` (the adapter registry key) is never editable here."""
    t = get_t(request)
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise AppError("provider_not_found", t("errors.provider_not_found"), status_code=404)

    name = name.strip()
    if not name:
        return render(
            request,
            "providers/list.html",
            {"rows": _provider_rows(db), "error": t("errors.provider_name_required")},
            status_code=400,
        )

    provider.name = name
    db.commit()
    return RedirectResponse(url="/providers", status_code=303)
