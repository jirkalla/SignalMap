"""PromptSet routes: create under a client, view detail, add prompts to it.

(docs/REQUIREMENTS.md FR-4..FR-6). List + create only — prompt editing is
deferred, per the project's prompt-versioning design.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Market, Prompt, PromptSet
from app.routers.clients import _get_client_or_404
from app.templating import get_t, render

router = APIRouter(tags=["prompt-sets"])


def _get_prompt_set_or_404(db: Session, request: Request, prompt_set_id: int) -> PromptSet:
    prompt_set = db.get(PromptSet, prompt_set_id)
    if prompt_set is None:
        raise AppError("prompt_set_not_found", get_t(request)("errors.prompt_set_not_found"), status_code=404)
    return prompt_set


@router.post("/clients/{client_id}/prompt-sets")
def create_prompt_set(
    request: Request,
    client_id: int,
    name: str = Form(..., description="Name for this group of prompts, e.g. 'Q1 2026 brand tracking'."),
    db: Session = Depends(get_db),
):
    """Create a new prompt set under a client (FR-4)."""
    client = _get_client_or_404(db, request, client_id)
    prompt_set = PromptSet(client_id=client.id, name=name.strip())
    db.add(prompt_set)
    db.commit()
    db.refresh(prompt_set)
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.get("/prompt-sets/{prompt_set_id}")
def prompt_set_detail(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Show one prompt set: its prompts (FR-6) and the add-prompt form."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    prompts = db.scalars(
        select(Prompt).where(Prompt.prompt_set_id == prompt_set_id).order_by(Prompt.created_at.desc())
    ).all()
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    market_options = [(m.id, f"{m.code} — {m.label}" if m.label else m.code) for m in markets]
    return render(
        request,
        "prompt_sets/detail.html",
        {"prompt_set": prompt_set, "prompts": prompts, "markets": market_options},
    )


@router.post("/prompt-sets/{prompt_set_id}/prompts")
def create_prompt(
    request: Request,
    prompt_set_id: int,
    text: str = Form(..., description="The exact question text sent to the AI provider."),
    market_id: int = Form(..., description="Which language/country market this prompt targets."),
    topic: str = Form("", description="Optional topic label for grouping/filtering prompts."),
    is_active: bool = Form(False, description="Inactive prompts are kept for history but not offered for new runs."),
    db: Session = Depends(get_db),
):
    """Add a new prompt to a prompt set (FR-5). Always created at version 1."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", "Selected market does not exist.", status_code=400)
    prompt = Prompt(
        prompt_set_id=prompt_set.id,
        text=text.strip(),
        market_id=market.id,
        topic=topic.strip() or None,
        is_active=is_active,
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}", status_code=303)
