"""AIModel admin: full CRUD on models under a provider (app/models/provider.py).

Unlike Provider (app/routers/providers.py), a model is pure data — no code
change is needed to add one, so create/edit/delete/toggle-active are all
offered here. Delete follows the same evidence-retention policy as
Client/Prompt/PromptSet (HD-T4): `runs.model_id` has no ondelete, so a model
referenced by any Run can only be deactivated, never deleted.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, AIModelPriceHistory, Provider, Run, User
from app.templating import get_t, render

# Editor + admin (docs/ROADMAP.md §1 follow-up) — unlike providers.py/users.py, model
# configuration (pricing, activation) is something an editor can also manage, not admin-only.
router = APIRouter(prefix="/ai-models", tags=["ai_models"], dependencies=[Depends(require_role("admin", "editor"))])

_CAPABILITY_TIERS = ("flagship", "standard", "economy")

# ai_models.cost_per_1k_*_usd is Numeric(10, 5) — 10 total digits, 5 after the
# decimal point, so the largest representable value is just under 10**5.
_MAX_PRICE_PER_1K = Decimal(100_000)
_PRICE_QUANTUM = Decimal("0.00001")


def _get_model_or_404(db: Session, request: Request, model_id: int) -> AIModel:
    model = db.get(AIModel, model_id)
    if model is None:
        raise AppError("ai_model_not_found", get_t(request)("errors.ai_model_not_found"), status_code=404)
    return model


def _model_rows(db: Session) -> list[tuple[AIModel, int]]:
    """Every model, grouped/ordered by provider, with its total run count (for the delete-block state)."""
    models = db.scalars(select(AIModel).join(Provider).order_by(Provider.name, AIModel.model_name)).all()
    run_counts = dict(db.execute(select(Run.model_id, func.count(Run.id)).group_by(Run.model_id)).all())
    return [(m, run_counts.get(m.id, 0)) for m in models]


def _model_run_count(db: Session, model_id: int) -> int:
    """How many runs exist against this one model — the delete-block check (same pattern as

    app/routers/clients.py's _client_run_count / app/routers/prompt_sets.py's _prompt_set_run_count).
    """
    return db.scalar(select(func.count(Run.id)).where(Run.model_id == model_id)) or 0


def _provider_options(db: Session) -> list[tuple[int, str]]:
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    return [(p.id, p.name) for p in providers]


def _model_to_form_state(model: AIModel) -> dict:
    """Shape an AIModel ORM row into the same dict keys the create/edit forms re-render on
    validation error, so ai_models/form.html only ever has one shape to read from — most
    importantly, `cost_per_1k_*_usd` (DB/storage unit) becomes `cost_per_million_*_usd`
    (the unit admins actually type, matching every provider's published pricing)."""
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "model_name": model.model_name,
        "display_name": model.display_name or "",
        "capability_tier": model.capability_tier,
        "cost_per_million_input_usd": str(model.cost_per_1k_input_usd * 1000) if model.cost_per_1k_input_usd is not None else "",
        "cost_per_million_output_usd": str(model.cost_per_1k_output_usd * 1000) if model.cost_per_1k_output_usd is not None else "",
        "context_window_tokens": str(model.context_window_tokens) if model.context_window_tokens is not None else "",
        "max_output_tokens": str(model.max_output_tokens) if model.max_output_tokens is not None else "",
        "is_free": model.is_free,
        "supports_web_search": model.supports_web_search,
        "notes": model.notes or "",
        "created_at": model.created_at,
    }


def _parse_price(raw: str, t) -> tuple[Decimal | None, str | None]:
    """Parse an optional per-million-token price into the per-1k value the DB stores.

    Empty input is valid (price not yet known — see docs/TASKS_PHASE2.md
    design decision 7). Admins enter the per-million figure since that's how
    every provider publishes pricing; storage stays per-1k for continuity
    with the phase-1 schema (`cost_per_1k_input_usd`/`cost_per_1k_output_usd`).

    Explicitly quantized to the column's actual scale (5 decimal places) and
    range-checked before it ever reaches the database — otherwise a
    sub-cent-per-million price (e.g. "0.075") gets silently rounded by
    Postgres with no warning, and a wildly out-of-range typo raises an
    uncaught NumericValueOutOfRange during commit instead of a clean 409.
    """
    raw = raw.strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw) / 1000
    except InvalidOperation:
        return None, t("errors.ai_model_invalid_price")
    if value < 0 or value >= _MAX_PRICE_PER_1K:
        return None, t("errors.ai_model_price_out_of_range")
    return value.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP), None


def _parse_int(raw: str, t) -> tuple[int | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, t("errors.ai_model_invalid_number")


def _validate_and_parse_model_form(
    db: Session,
    t,
    *,
    provider_id: int,
    model_name: str,
    capability_tier: str,
    cost_per_million_input_usd: str,
    cost_per_million_output_usd: str,
    context_window_tokens: str,
    max_output_tokens: str,
    exclude_id: int | None = None,
) -> tuple[dict, str | None]:
    """Validate + parse one create/edit submission. Shared by create_ai_model and

    update_ai_model so the two routes' validation rules can't drift apart —
    same shared-validator shape as app/routers/markets.py's
    _validate_iso_format (used by both create_market and update_market).

    Returns (parsed, error). `parsed` always has keys cost_in/cost_out/
    context_tokens/max_tokens; they're only meaningful when error is None.
    `exclude_id` excludes the model being edited from the name-conflict check.
    """
    parsed: dict = {"cost_in": None, "cost_out": None, "context_tokens": None, "max_tokens": None}

    if db.get(Provider, provider_id) is None:
        return parsed, t("errors.provider_not_found")
    if not model_name:
        return parsed, t("errors.ai_model_name_required")
    if capability_tier not in _CAPABILITY_TIERS:
        return parsed, t("errors.ai_model_invalid_tier")

    parsed["cost_in"], error = _parse_price(cost_per_million_input_usd, t)
    if error:
        return parsed, error
    parsed["cost_out"], error = _parse_price(cost_per_million_output_usd, t)
    if error:
        return parsed, error
    parsed["context_tokens"], error = _parse_int(context_window_tokens, t)
    if error:
        return parsed, error
    parsed["max_tokens"], error = _parse_int(max_output_tokens, t)
    if error:
        return parsed, error

    conflict_query = select(AIModel).where(AIModel.provider_id == provider_id, AIModel.model_name == model_name)
    if exclude_id is not None:
        conflict_query = conflict_query.where(AIModel.id != exclude_id)
    if db.scalar(conflict_query) is not None:
        return parsed, t("errors.ai_model_name_conflict")

    return parsed, None


@router.get("")
def list_ai_models(request: Request, db: Session = Depends(get_db)):
    """List every AI model across all providers, with pricing, capability tier, and run-based delete eligibility."""
    return render(request, "ai_models/list.html", {"rows": _model_rows(db)})


@router.get("/new")
def new_ai_model_form(request: Request, db: Session = Depends(get_db)):
    """Render the empty model-creation form."""
    t = get_t(request)
    return render(
        request,
        "ai_models/form.html",
        {
            "title": t("ai_model.create_title"),
            "action": "/ai-models",
            "cancel_url": "/ai-models",
            "model": None,
            "providers": _provider_options(db),
        },
    )


@router.post("")
def create_ai_model(
    request: Request,
    provider_id: int = Form(..., description="Which provider this model belongs to."),
    model_name: str = Form(..., description="Exact model string passed to the provider adapter, e.g. 'claude-sonnet-5'."),
    display_name: str = Form("", description="Human-readable name shown in the UI, e.g. 'Claude Sonnet 5'."),
    capability_tier: str = Form(..., description="One of: flagship, standard, economy."),
    cost_per_million_input_usd: str = Form(
        "", description="Price per 1M input tokens in USD, e.g. '2.00'. Leave empty if not yet known."
    ),
    cost_per_million_output_usd: str = Form(
        "", description="Price per 1M output tokens in USD, e.g. '10.00'. Leave empty if not yet known."
    ),
    context_window_tokens: str = Form("", description="Total context window in tokens, e.g. '1000000'."),
    max_output_tokens: str = Form("", description="Maximum output tokens per response, e.g. '128000'."),
    is_free: bool = Form(False, description="Whether this model is genuinely free to call — never inferred from price."),
    supports_web_search: bool = Form(False, description="Whether this model's adapter enables provider-side web search/grounding."),
    notes: str = Form("", description="Free-text admin notes, e.g. pricing source and verification date."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Create a new model under a provider. Model rows are pure data — no adapter change is required.

    Writes the model's first `AIModelPriceHistory` row (its initial price, whatever that is —
    including both `NULL`) in the same commit, so every model's price timeline starts at its
    own creation, never at the moment someone first edits its price.
    """
    t = get_t(request)
    model_name = model_name.strip()
    display_name = display_name.strip()
    notes = notes.strip()
    form_state = {
        "provider_id": provider_id,
        "model_name": model_name,
        "display_name": display_name,
        "capability_tier": capability_tier,
        "cost_per_million_input_usd": cost_per_million_input_usd,
        "cost_per_million_output_usd": cost_per_million_output_usd,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "is_free": is_free,
        "supports_web_search": supports_web_search,
        "notes": notes,
    }

    parsed, error = _validate_and_parse_model_form(
        db,
        t,
        provider_id=provider_id,
        model_name=model_name,
        capability_tier=capability_tier,
        cost_per_million_input_usd=cost_per_million_input_usd,
        cost_per_million_output_usd=cost_per_million_output_usd,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
    )

    if error:
        return render(
            request,
            "ai_models/form.html",
            {
                "title": t("ai_model.create_title"),
                "action": "/ai-models",
                "cancel_url": "/ai-models",
                "model": form_state,
                "providers": _provider_options(db),
                "error": error,
            },
            status_code=409,
        )

    model = AIModel(
        provider_id=provider_id,
        model_name=model_name,
        display_name=display_name or None,
        capability_tier=capability_tier,
        cost_per_1k_input_usd=parsed["cost_in"],
        cost_per_1k_output_usd=parsed["cost_out"],
        context_window_tokens=parsed["context_tokens"],
        max_output_tokens=parsed["max_tokens"],
        is_free=is_free,
        supports_web_search=supports_web_search,
        notes=notes or None,
    )
    db.add(model)
    db.flush()  # need model.id before the history row can reference it
    db.add(
        AIModelPriceHistory(
            ai_model_id=model.id,
            cost_per_1k_input_usd=model.cost_per_1k_input_usd,
            cost_per_1k_output_usd=model.cost_per_1k_output_usd,
            changed_by_user_id=user.id,
        )
    )
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)


@router.get("/{model_id}/edit")
def edit_ai_model_form(request: Request, model_id: int, db: Session = Depends(get_db)):
    """Render the model edit form, pre-filled with current values, including read-only created_at."""
    model = _get_model_or_404(db, request, model_id)
    t = get_t(request)
    return render(
        request,
        "ai_models/form.html",
        {
            "title": t("ai_model.edit_title"),
            "action": f"/ai-models/{model_id}/edit",
            "cancel_url": "/ai-models",
            "model": _model_to_form_state(model),
            "providers": _provider_options(db),
        },
    )


@router.post("/{model_id}/edit")
def update_ai_model(
    request: Request,
    model_id: int,
    provider_id: int = Form(..., description="Which provider this model belongs to."),
    model_name: str = Form(..., description="Exact model string passed to the provider adapter."),
    display_name: str = Form("", description="Human-readable name shown in the UI."),
    capability_tier: str = Form(..., description="One of: flagship, standard, economy."),
    cost_per_million_input_usd: str = Form("", description="Price per 1M input tokens in USD. Leave empty if not yet known."),
    cost_per_million_output_usd: str = Form("", description="Price per 1M output tokens in USD. Leave empty if not yet known."),
    context_window_tokens: str = Form("", description="Total context window in tokens."),
    max_output_tokens: str = Form("", description="Maximum output tokens per response."),
    is_free: bool = Form(False, description="Whether this model is genuinely free to call."),
    supports_web_search: bool = Form(False, description="Whether this model's adapter enables provider-side web search/grounding."),
    notes: str = Form("", description="Free-text admin notes."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Update an existing model. `model_name` stays editable — unlike Prompt, a model has no run-time evidence tying to its exact text, only to its id (Run.model_id).

    Writes a new `AIModelPriceHistory` row only when the price actually changes (either
    `cost_per_1k_input_usd` or `cost_per_1k_output_usd` differs from what's saved) — editing an
    unrelated field like `notes` never adds one.
    """
    t = get_t(request)
    model = _get_model_or_404(db, request, model_id)
    model_name = model_name.strip()
    display_name = display_name.strip()
    notes = notes.strip()

    parsed, error = _validate_and_parse_model_form(
        db,
        t,
        provider_id=provider_id,
        model_name=model_name,
        capability_tier=capability_tier,
        cost_per_million_input_usd=cost_per_million_input_usd,
        cost_per_million_output_usd=cost_per_million_output_usd,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        exclude_id=model_id,
    )

    if error:
        form_state = {
            "id": model_id,
            "provider_id": provider_id,
            "model_name": model_name,
            "display_name": display_name,
            "capability_tier": capability_tier,
            "cost_per_million_input_usd": cost_per_million_input_usd,
            "cost_per_million_output_usd": cost_per_million_output_usd,
            "context_window_tokens": context_window_tokens,
            "max_output_tokens": max_output_tokens,
            "is_free": is_free,
            "supports_web_search": supports_web_search,
            "notes": notes,
            "created_at": model.created_at,
        }
        return render(
            request,
            "ai_models/form.html",
            {
                "title": t("ai_model.edit_title"),
                "action": f"/ai-models/{model_id}/edit",
                "cancel_url": "/ai-models",
                "model": form_state,
                "providers": _provider_options(db),
                "error": error,
            },
            status_code=409,
        )

    price_changed = (
        parsed["cost_in"] != model.cost_per_1k_input_usd or parsed["cost_out"] != model.cost_per_1k_output_usd
    )

    model.provider_id = provider_id
    model.model_name = model_name
    model.display_name = display_name or None
    model.capability_tier = capability_tier
    model.cost_per_1k_input_usd = parsed["cost_in"]
    model.cost_per_1k_output_usd = parsed["cost_out"]
    model.context_window_tokens = parsed["context_tokens"]
    model.max_output_tokens = parsed["max_tokens"]
    model.is_free = is_free
    model.supports_web_search = supports_web_search
    model.notes = notes or None
    if price_changed:
        db.add(
            AIModelPriceHistory(
                ai_model_id=model.id,
                cost_per_1k_input_usd=parsed["cost_in"],
                cost_per_1k_output_usd=parsed["cost_out"],
                changed_by_user_id=user.id,
            )
        )
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)


@router.post("/{model_id}/toggle-active")
def toggle_ai_model_active(request: Request, model_id: int, db: Session = Depends(get_db)):
    """Flip a model's `is_active` flag. Never touches evidence — a run made while active stays exactly as recorded."""
    model = _get_model_or_404(db, request, model_id)
    model.is_active = not model.is_active
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)


@router.post("/{model_id}/delete")
def delete_ai_model(request: Request, model_id: int, db: Session = Depends(get_db)):
    """Delete a model — blocked (inline error, not a raw API error) if any run was made against it.

    `runs.model_id` has no ondelete, so a model with recorded runs can only
    be deactivated (see toggle_ai_model_active), never deleted — deleting it
    would strand those runs' evidence of which model actually produced them.
    """
    t = get_t(request)
    model = _get_model_or_404(db, request, model_id)
    run_count = _model_run_count(db, model_id)
    if run_count:
        return render(
            request,
            "ai_models/list.html",
            {"rows": _model_rows(db), "error": t("errors.ai_model_in_use").format(count=run_count)},
            status_code=409,
        )
    db.delete(model)
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)
