"""Prompt detail route: shows the prompt, the run-trigger form, and its run history."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import ADAPTERS
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Prompt, Provider, Run
from app.templating import get_t, render

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _get_prompt_or_404(db: Session, request: Request, prompt_id: int) -> Prompt:
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        raise AppError("prompt_not_found", get_t(request)("errors.prompt_not_found"), status_code=404)
    return prompt


def _runnable_model_groups(db: Session) -> list[tuple[str, list[tuple[int, str]]]]:
    """Active models, grouped by provider, for the run-trigger dropdown.

    A provider only contributes a group once it has both a registered
    adapter (app.adapters.ADAPTERS) and at least one active model — a
    provider row with no adapter yet, or no models yet (e.g. Anthropic
    added ahead of its adapter), simply produces no group rather than a
    broken or dead option.
    """
    models = db.scalars(
        select(AIModel).join(Provider).where(AIModel.is_active.is_(True)).order_by(Provider.name, AIModel.display_name)
    ).all()
    groups: dict[str, list[tuple[int, str]]] = {}
    for model in models:
        if model.provider.code not in ADAPTERS:
            continue
        groups.setdefault(model.provider.name, []).append((model.id, model.display_name or model.model_name))
    return list(groups.items())


@router.get("/{prompt_id}")
def prompt_detail(request: Request, prompt_id: int, db: Session = Depends(get_db)):
    """Show one prompt: its text/market/topic, a run-trigger form, and past runs (FR-7, FR-15)."""
    prompt = _get_prompt_or_404(db, request, prompt_id)
    model_groups = _runnable_model_groups(db)
    runs = db.scalars(
        select(Run).where(Run.prompt_id == prompt_id).order_by(Run.started_at.desc())
    ).all()
    return render(
        request,
        "prompts/detail.html",
        {"prompt": prompt, "model_groups": model_groups, "runs": runs},
    )
