"""Prompt detail/edit routes: shows the prompt, the run-trigger form, its run

history, and lets it be edited — as a new version, never in place (NFR-6).
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
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


def _runnable_model_groups(db: Session) -> list[tuple[str, list[AIModel]]]:
    """Active models, grouped by provider, for the run-trigger dropdown.

    A provider only contributes a group once it has both a registered
    adapter (app.adapters.ADAPTERS) and at least one active model — a
    provider row with no adapter yet, or no models yet (e.g. Anthropic
    added ahead of its adapter), simply produces no group rather than a
    broken or dead option.

    Returns full AIModel rows (not just id/label pairs) — prompts/detail.html
    builds the <option>s itself (not select_grouped_field, which only knows
    generic (value, text) pairs) so it can also pre-render each model's
    cost_badge for the live price/free indicator below the dropdown.
    """
    models = db.scalars(
        select(AIModel).join(Provider).where(AIModel.is_active.is_(True)).order_by(Provider.name, AIModel.display_name)
    ).all()
    groups: dict[str, list[AIModel]] = {}
    for model in models:
        if model.provider.code not in ADAPTERS:
            continue
        groups.setdefault(model.provider.name, []).append(model)
    return list(groups.items())


def _version_history(db: Session, prompt: Prompt) -> list[Prompt]:
    """Every version of `prompt`'s lineage, newest first — empty extra work if there's only one."""
    root_id = prompt.root_prompt_id or prompt.id
    return db.scalars(
        select(Prompt)
        .where(or_(Prompt.id == root_id, Prompt.root_prompt_id == root_id))
        .order_by(Prompt.version.desc())
    ).all()


def _lineage_run_count(db: Session, lineage_ids: list[int]) -> int:
    """How many runs exist against any version in this prompt's lineage — the delete-block check."""
    return db.scalar(select(func.count(Run.id)).where(Run.prompt_id.in_(lineage_ids))) or 0


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


@router.post("/{prompt_id}/delete")
def delete_prompt(request: Request, prompt_id: int, db: Session = Depends(get_db)):
    """Delete this prompt's entire version lineage, unless any version has a recorded run.

    A prompt's edit history (NFR-6) is a single logical entity split across
    rows by `root_prompt_id` — deleting only the current version and
    leaving old versions orphaned would be a confusing partial state, so
    delete always targets the whole lineage (root + every version sharing
    its root_prompt_id), and blocks if ANY version in it has a run.
    """
    t = get_t(request)
    prompt = _get_prompt_or_404(db, request, prompt_id)
    versions = _version_history(db, prompt)
    lineage_ids = [v.id for v in versions]
    run_count = _lineage_run_count(db, lineage_ids)
    if run_count:
        model_groups = _runnable_model_groups(db)
        runs = db.scalars(select(Run).where(Run.prompt_id == prompt_id).order_by(Run.started_at.desc())).all()
        return render(
            request,
            "prompts/detail.html",
            {
                "prompt": prompt,
                "model_groups": model_groups,
                "markets": market_options(db),
                "runs": runs,
                "versions": versions if len(versions) > 1 else [],
                "error": t("errors.prompt_in_use").format(count=run_count),
            },
            status_code=409,
        )
    prompt_set_id = prompt.prompt_set_id
    # Children (root_prompt_id set) must go before the root row they reference.
    for v in sorted(versions, key=lambda p: p.root_prompt_id is None):
        db.delete(v)
    db.commit()
    return RedirectResponse(url=f"/prompt-sets/{prompt_set_id}", status_code=303)
