"""Prompt detail route: shows the prompt, the run-trigger form, and its run history."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Prompt, Run
from app.templating import get_t, render

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _get_prompt_or_404(db: Session, request: Request, prompt_id: int) -> Prompt:
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        raise AppError("prompt_not_found", get_t(request)("errors.prompt_not_found"), status_code=404)
    return prompt


@router.get("/{prompt_id}")
def prompt_detail(request: Request, prompt_id: int, db: Session = Depends(get_db)):
    """Show one prompt: its text/market/topic, a run-trigger form, and past runs (FR-7, FR-15)."""
    prompt = _get_prompt_or_404(db, request, prompt_id)
    models = db.scalars(select(AIModel).where(AIModel.is_active.is_(True)).order_by(AIModel.display_name)).all()
    model_options = [(m.id, m.display_name or m.model_name) for m in models]
    runs = db.scalars(
        select(Run).where(Run.prompt_id == prompt_id).order_by(Run.started_at.desc())
    ).all()
    return render(
        request,
        "prompts/detail.html",
        {"prompt": prompt, "models": model_options, "runs": runs},
    )
