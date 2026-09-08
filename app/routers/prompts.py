"""Prompt detail/edit routes: shows the prompt, the run-trigger form, its run

history, and lets it be edited — as a new version, never in place (NFR-6).
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters import ADAPTERS
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, Market, Prompt, Provider, Run
from app.templating import get_t, render
from app.utils import market_options

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


def _version_history(db: Session, prompt: Prompt) -> list[Prompt]:
    """Every version of `prompt`'s lineage, newest first — empty extra work if there's only one."""
    root_id = prompt.root_prompt_id or prompt.id
    return db.scalars(
        select(Prompt)
        .where(or_(Prompt.id == root_id, Prompt.root_prompt_id == root_id))
        .order_by(Prompt.version.desc())
    ).all()


@router.get("/{prompt_id}")
def prompt_detail(request: Request, prompt_id: int, db: Session = Depends(get_db)):
    """Show one prompt: its text/market/topic, a run-trigger form, past runs, and version history (FR-7, FR-15)."""
    prompt = _get_prompt_or_404(db, request, prompt_id)
    model_groups = _runnable_model_groups(db)
    runs = db.scalars(
        select(Run).where(Run.prompt_id == prompt_id).order_by(Run.started_at.desc())
    ).all()
    versions = _version_history(db, prompt)
    return render(
        request,
        "prompts/detail.html",
        {
            "prompt": prompt,
            "model_groups": model_groups,
            "markets": market_options(db),
            "runs": runs,
            "versions": versions if len(versions) > 1 else [],
        },
    )


@router.get("/{prompt_id}/edit")
def edit_prompt_form(request: Request, prompt_id: int, db: Session = Depends(get_db)):
    """Render the prompt edit form. Saving creates version+1 — this row is never changed."""
    prompt = _get_prompt_or_404(db, request, prompt_id)
    t = get_t(request)
    return render(
        request,
        "prompts/form.html",
        {
            "title": t("prompt.edit_title"),
            "action": f"/prompts/{prompt_id}/edit",
            "cancel_url": f"/prompts/{prompt_id}",
            "prompt": prompt,
            "markets": market_options(db),
            "next_version": prompt.version + 1,
        },
    )


@router.post("/{prompt_id}/edit")
def update_prompt(
    request: Request,
    prompt_id: int,
    text: str = Form(..., description="The exact question text sent to the AI provider."),
    market_id: int = Form(..., description="Which language/country market this prompt targets."),
    topic: str = Form("", description="Optional topic label for grouping/filtering prompts."),
    is_active: bool = Form(False, description="Inactive prompts are kept for history but not offered for new runs."),
    db: Session = Depends(get_db),
):
    """Save an edit as a new prompt version (FR-6's deferred edit UI, now built).

    The original row is never modified — a new row is inserted at
    version+1 and the original is marked as no longer current, so every
    past run still shows the exact text it actually ran against.
    """
    t = get_t(request)
    old = _get_prompt_or_404(db, request, prompt_id)
    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", t("errors.market_not_found"), status_code=400)

    new_prompt = Prompt(
        prompt_set_id=old.prompt_set_id,
        root_prompt_id=old.root_prompt_id or old.id,
        version=old.version + 1,
        text=text.strip(),
        market_id=market.id,
        topic=topic.strip() or None,
        is_active=is_active,
    )
    old.is_current_version = False
    db.add(new_prompt)
    db.commit()
    db.refresh(new_prompt)
    return RedirectResponse(url=f"/prompts/{new_prompt.id}", status_code=303)
