"""PromptSet routes: create under a client, view detail, add prompts to it.

(docs/REQUIREMENTS.md FR-4..FR-6). Prompt editing lives on the prompt
detail route (app/routers/prompts.py) since editing creates a new version
rather than changing anything here.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Market, Prompt, PromptSet, Run
from app.routers.clients import _get_client_or_404
from app.routers.prompts import _delete_prompt_lineage
from app.templating import get_t, render
from app.utils import market_options

router = APIRouter(tags=["prompt-sets"])


def _get_prompt_set_or_404(db: Session, request: Request, prompt_set_id: int) -> PromptSet:
    prompt_set = db.get(PromptSet, prompt_set_id)
    if prompt_set is None:
        raise AppError("prompt_set_not_found", get_t(request)("errors.prompt_set_not_found"), status_code=404)
    return prompt_set


def _prompt_set_run_count(db: Session, prompt_set_id: int) -> int:
    """How many runs exist under any prompt of this prompt set — the delete-block check."""
    return (
        db.scalar(
            select(func.count(Run.id)).join(Prompt, Run.prompt_id == Prompt.id).where(Prompt.prompt_set_id == prompt_set_id)
        )
        or 0
    )


def _current_prompts(db: Session, prompt_set_id: int) -> list[Prompt]:
    """This prompt set's current-version prompts, newest first — shared by the detail and delete-blocked views."""
    return db.scalars(
        select(Prompt)
        .where(Prompt.prompt_set_id == prompt_set_id, Prompt.is_current_version.is_(True))
        .order_by(Prompt.created_at.desc())
    ).all()


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
    """Show one prompt set: its current-version prompts (FR-6) and the add-prompt form.

    Superseded versions (see app/models/prompt.py) are omitted here — reach
    them via the "version history" on a current prompt's detail page.
    """
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    prompts = _current_prompts(db, prompt_set_id)
    return render(
        request,
        "prompt_sets/detail.html",
        {"prompt_set": prompt_set, "prompts": prompts, "markets": market_options(db)},
    )


@router.get("/prompt-sets/{prompt_set_id}/edit")
def edit_prompt_set_form(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Render the prompt-set edit form, pre-filled with the current name."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    t = get_t(request)
    return render(
        request,
        "prompt_sets/form.html",
        {
            "title": t("prompt_set.edit_title"),
            "action": f"/prompt-sets/{prompt_set_id}/edit",
            "cancel_url": f"/prompt-sets/{prompt_set_id}",
            "prompt_set": prompt_set,
        },
    )


@router.post("/prompt-sets/{prompt_set_id}/edit")
def update_prompt_set(
    request: Request,
    prompt_set_id: int,
    name: str = Form(..., description="Name for this group of prompts, e.g. 'Q1 2026 brand tracking'."),
    db: Session = Depends(get_db),
):
    """Update a prompt set's name."""
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    prompt_set.name = name.strip()
    db.commit()
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}", status_code=303)


@router.post("/prompt-sets/{prompt_set_id}/delete")
def delete_prompt_set(request: Request, prompt_set_id: int, db: Session = Depends(get_db)):
    """Delete a prompt set and its prompts, unless any of them has a recorded run.

    Blocked (inline error, not a raw API error) the same way client and
    market delete are — deleting evidence is never allowed (NFR-6).

    Prompts are deleted via `_delete_prompt_lineage` rather than left to a
    bare `db.delete(prompt_set)` cascade — see that helper's docstring
    (app/routers/prompts.py) for why a plain cascade would violate
    `fk_prompts_root_prompt_id_prompts` on any prompt with edit history.
    """
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    run_count = _prompt_set_run_count(db, prompt_set_id)
    if run_count:
        return render(
            request,
            "prompt_sets/detail.html",
            {
                "prompt_set": prompt_set,
                "prompts": _current_prompts(db, prompt_set_id),
                "markets": market_options(db),
                "error": t("errors.prompt_set_in_use").format(count=run_count),
            },
            status_code=409,
        )
    client_id = prompt_set.client_id
    prompts = db.scalars(select(Prompt).where(Prompt.prompt_set_id == prompt_set_id)).all()
    _delete_prompt_lineage(db, prompts)
    db.delete(prompt_set)
    db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


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
    t = get_t(request)
    prompt_set = _get_prompt_set_or_404(db, request, prompt_set_id)
    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)
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
