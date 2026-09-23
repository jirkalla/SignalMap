"""AIModel admin: full CRUD on models under a provider (app/models/provider.py).

Unlike Provider (app/routers/providers.py), a model is pure data — no code
change is needed to add one, so create/edit/delete/toggle-active are all
offered here. Delete follows the same evidence-retention policy as
Client/Prompt/PromptSet (HD-T4): `runs.model_id` has no ondelete, so a model
referenced by any Run can only be deactivated, never deleted.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import AIModel, AIModelPriceComponent, Provider, Run, User
from app.models.provider import COMPONENT_TYPES
from app.services.cost import current_prices, prices_at
from app.templating import get_t, render

# Editor + admin (docs/ROADMAP.md §1 follow-up) — unlike providers.py/users.py, model
# configuration (pricing, activation) is something an editor can also manage, not admin-only.
router = APIRouter(prefix="/ai-models", tags=["ai_models"], dependencies=[Depends(require_role("admin", "editor"))])

_CAPABILITY_TIERS = ("flagship", "standard", "economy")

# ai_model_price_components.price_per_unit_usd is Numeric(12, 6) — 12 total digits, 6 after the
# decimal point, so the largest representable value is just under 10**6 (docs/
# TASKS_COST_COMPONENTS.md design decision 11).
_MAX_PRICE_PER_1M = Decimal(1_000_000)
_PRICE_PER_1M_QUANTUM = Decimal("0.000001")


def _get_model_or_404(db: Session, request: Request, model_id: int) -> AIModel:
    model = db.get(AIModel, model_id)
    if model is None:
        raise AppError("ai_model_not_found", get_t(request)("errors.ai_model_not_found"), status_code=404)
    return model


def _model_rows(db: Session) -> list[tuple[AIModel, int, dict[str, Decimal]]]:
    """Every model, grouped/ordered by provider, with its total run count (for the delete-block
    state) and today's component prices (for cost_badge, app/templates/partials/macros.html).
    """
    models = db.scalars(select(AIModel).join(Provider).order_by(Provider.name, AIModel.model_name)).all()
    run_counts = dict(db.execute(select(Run.model_id, func.count(Run.id)).group_by(Run.model_id)).all())
    prices = current_prices(db, [m.id for m in models])
    return [(m, run_counts.get(m.id, 0), prices.get(m.id, {})) for m in models]


def _grouped_model_rows(rows: list[tuple[AIModel, int, dict[str, Decimal]]]) -> list[tuple[str, list[tuple[AIModel, int, dict[str, Decimal]]]]]:
    """`_model_rows`'s flat, already-provider-ordered list, folded into (provider_name, rows)
    groups for list.html's per-provider section headers — six providers as of NP-T4 made the flat
    table hard to scan (found while building that branch, docs/TASKS_NEW_PROVIDERS.md), the rows
    always blending together with no visual break between providers.

    A plain dict keyed by provider name (not `itertools.groupby`, which needs pre-sorted input AND
    silently produces a new group per *adjacent* run rather than merging same-keyed groups that
    aren't contiguous) — `_model_rows`'s own `ORDER BY Provider.name` already keeps a provider's
    rows contiguous, but a dict is one line simpler here and doesn't depend on that ordering
    invariant holding forever. Same "group by, insertion order" idiom as
    app/routers/prompts.py's `_runnable_model_groups`.
    """
    groups: dict[str, list[tuple[AIModel, int, dict[str, Decimal]]]] = {}
    for row in rows:
        model = row[0]
        groups.setdefault(model.provider.name, []).append(row)
    return list(groups.items())


def _model_run_count(db: Session, model_id: int) -> int:
    """How many runs exist against this one model — the delete-block check (same pattern as

    app/routers/clients.py's _client_run_count / app/routers/prompt_sets.py's _prompt_set_run_count).
    """
    return db.scalar(select(func.count(Run.id)).where(Run.model_id == model_id)) or 0


def _provider_options(db: Session) -> list[tuple[int, str]]:
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    return [(p.id, p.name) for p in providers]


@dataclass
class PriceSpan:
    """One continuous stretch of time during which a component cost the same, for display only.

    Not a database row: several `ai_model_price_components` rows collapse into one span when they
    carry the same price back to back (see `_price_history_rows`).
    """

    component_type: str
    price_per_unit_usd: Decimal
    valid_from: datetime
    valid_until: datetime | None


def _price_history_rows(model: AIModel) -> list[tuple[str, list[PriceSpan]]]:
    """`model`'s price history as `(component_type, spans)` groups, newest span first within each.

    Two deliberate differences from a plain listing of `ai_model_price_components`, both because a
    raw listing turned out to be actively misleading once backdating existed (reported from the
    edit page, 2026-09-19):

    **Grouped by component type, not interleaved by date.** A row's "valid until" is the next-newer
    row's `effective_from` *for its own component_type* — so the newest row of every type is
    current, and a model with input, output and cache_read prices legitimately has three rows
    reading "current" at once. Sorted by date across all types, as this used to be, those three
    look like three competing answers to one question instead of today's price of three different
    things. Computed per type either way (docs/TASKS_COST_COMPONENTS.md CC-3 step 5), so a change
    to `input` never makes an untouched `cache_read` row look expired.

    **Adjacent rows with the same price are merged into one span.** Backdating writes a row even
    when the value equals the one already in effect — it has to, since "this price also applied
    last week" is information the database did not previously hold (docs/TASKS_PRE_SCHEDULER.md
    design decision 7). Displayed one row per record, that draws a boundary at the moment the
    price was *entered*, where the price itself did not change, and a reader stops to look for a
    change that isn't there. The records are all kept; only the timeline is coalesced, which is the
    usual convention for showing a versioned value to a human.

    Groups follow `COMPONENT_TYPES` order rather than the data's own, so the list reads the same
    way on every model regardless of which components it happens to price.
    """
    rows_by_type: dict[str, list[AIModelPriceComponent]] = {}
    for component in model.price_components:  # newest-first overall, per the relationship's order_by
        rows_by_type.setdefault(component.component_type, []).append(component)

    groups: list[tuple[str, list[PriceSpan]]] = []
    for component_type in COMPONENT_TYPES:
        rows = rows_by_type.get(component_type)
        if not rows:
            continue
        spans: list[PriceSpan] = []
        for i, row in enumerate(rows):
            if spans and spans[-1].price_per_unit_usd == row.price_per_unit_usd:
                # Same price as the span above: this older row just extends it further back. Its
                # own valid_until is dropped on purpose — that is the boundary being merged away.
                spans[-1].valid_from = row.effective_from
                continue
            spans.append(
                PriceSpan(
                    component_type=component_type,
                    price_per_unit_usd=row.price_per_unit_usd,
                    valid_from=row.effective_from,
                    # None for the newest row of this type: still in effect today.
                    valid_until=rows[i - 1].effective_from if i > 0 else None,
                )
            )
        groups.append((component_type, spans))
    return groups


def _price_form_fields(prices: dict[str, str]) -> dict[str, str]:
    """`{'input': '2.00', ...}` -> `{'price_input': '2.00', ...}` — the form field name prefix,
    shared by every place that builds ai_models/form.html's re-render context."""
    return {f"price_{component_type}": prices.get(component_type, "") for component_type in COMPONENT_TYPES}


def _model_to_form_state(model: AIModel) -> dict:
    """Shape an AIModel ORM row into the same dict keys the create/edit forms re-render on
    validation error, so ai_models/form.html only ever has one shape to read from — one
    `price_<component_type>` field per entry in `COMPONENT_TYPES`, holding today's price
    (app.services.cost.prices_at) already in the per-1M-token unit admins type, matching every
    provider's published pricing (docs/TASKS_COST_COMPONENTS.md design decision 11)."""
    today_prices = prices_at(model.price_components, datetime.now(timezone.utc))
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "model_name": model.model_name,
        "display_name": model.display_name or "",
        "capability_tier": model.capability_tier,
        **_price_form_fields({ct: str(price) for ct, price in today_prices.items()}),
        "context_window_tokens": str(model.context_window_tokens) if model.context_window_tokens is not None else "",
        "max_output_tokens": str(model.max_output_tokens) if model.max_output_tokens is not None else "",
        "is_free": model.is_free,
        "supports_web_search": model.supports_web_search,
        "notes": model.notes or "",
        "created_at": model.created_at,
    }


def _parse_component_price(raw: str, t) -> tuple[Decimal | None, str | None]:
    """Parse an optional USD-per-1M-token component price.

    Empty input is valid — "we don't know this component's price yet", or "this provider doesn't
    bill this component" (docs/TASKS_PHASE2.md design decision 7; docs/TASKS_COST_COMPONENTS.md
    design decision 14: no row is the correct representation of "unknown", never a zero price).
    Stored directly per 1M tokens — unlike the retired `_parse_price`, this never divides by
    1000 — because that's both the unit every provider publishes pricing in and the unit
    `ai_model_price_components.price_per_unit_usd` is defined in (design decision 11).

    Explicitly quantized to the column's actual scale (6 decimal places) and range-checked before
    it ever reaches the database — otherwise a sub-cent-per-million price (e.g. Gemini 3.1
    flash-lite's cache-read price, "0.025") gets silently rounded by Postgres with no warning, and
    a wildly out-of-range typo raises an uncaught NumericValueOutOfRange during commit instead of
    a clean 409.
    """
    raw = raw.strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, t("errors.ai_model_invalid_price")
    if value < 0 or value >= _MAX_PRICE_PER_1M:
        return None, t("errors.ai_model_price_out_of_range")
    return value.quantize(_PRICE_PER_1M_QUANTUM, rounding=ROUND_HALF_UP), None


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
    prices: dict[str, str],
    context_window_tokens: str,
    max_output_tokens: str,
    exclude_id: int | None = None,
    current_component_prices: dict[str, Decimal] | None = None,
) -> tuple[dict, str | None]:
    """Validate + parse one create/edit submission. Shared by create_ai_model and

    update_ai_model so the two routes' validation rules can't drift apart —
    same shared-validator shape as app/routers/markets.py's
    _validate_iso_format (used by both create_market and update_market).

    Returns (parsed, error). `parsed` always has keys prices/context_tokens/max_tokens; they're
    only meaningful when error is None. `parsed["prices"]` has one entry per `COMPONENT_TYPES`
    value, `None` where that component wasn't submitted. `exclude_id` excludes the model being
    edited from the name-conflict check.

    `current_component_prices` (from `app.services.cost.prices_at`, empty/`None` on create — a
    new model has no prior price to clear) makes clearing an already-priced component a
    validation error rather than a silent no-op: `ai_model_price_components` is append-only and
    has no way to record "this component is no longer billed" except a new row, and an empty
    field is indistinguishable from a typo (docs/TASKS_COST_COMPONENTS.md CC-3 step 4). If the
    provider genuinely stopped billing a component, the admin enters 0, not a blank field.
    """
    parsed: dict = {"prices": {ct: None for ct in COMPONENT_TYPES}, "context_tokens": None, "max_tokens": None}
    current_component_prices = current_component_prices or {}

    if db.get(Provider, provider_id) is None:
        return parsed, t("errors.provider_not_found")
    if not model_name:
        return parsed, t("errors.ai_model_name_required")
    if capability_tier not in _CAPABILITY_TIERS:
        return parsed, t("errors.ai_model_invalid_tier")

    for component_type in COMPONENT_TYPES:
        value, error = _parse_component_price(prices.get(component_type, ""), t)
        if error:
            return parsed, error
        if value is None and component_type in current_component_prices:
            return parsed, t("errors.ai_model_price_component_cannot_be_cleared")
        parsed["prices"][component_type] = value

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
    """List every AI model, grouped by provider, with pricing, capability tier, and run-based delete eligibility."""
    return render(request, "ai_models/list.html", {"grouped_rows": _grouped_model_rows(_model_rows(db))})


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
    price_input: str = Form("", description="Price per 1M input tokens in USD, e.g. '2.00'. Leave empty if not yet known."),
    price_output: str = Form("", description="Price per 1M output tokens in USD, e.g. '10.00'. Leave empty if not yet known."),
    price_cache_read: str = Form("", description="Price per 1M cache-read tokens in USD. Leave empty if this provider doesn't bill it, or the price isn't yet known."),
    price_cache_write: str = Form("", description="Price per 1M cache-write tokens in USD (single-tier providers). Leave empty if this provider doesn't bill it, or the price isn't yet known."),
    price_cache_write_5m: str = Form("", description="Price per 1M cache-write tokens, 5-minute tier (Anthropic). Leave empty if not applicable."),
    price_cache_write_1h: str = Form("", description="Price per 1M cache-write tokens, 1-hour tier (Anthropic). Leave empty if not applicable."),
    context_window_tokens: str = Form("", description="Total context window in tokens, e.g. '1000000'."),
    max_output_tokens: str = Form("", description="Maximum output tokens per response, e.g. '128000'."),
    is_free: bool = Form(False, description="Whether this model is genuinely free to call — never inferred from price."),
    supports_web_search: bool = Form(False, description="Whether this model's adapter enables provider-side web search/grounding."),
    notes: str = Form("", description="Free-text admin notes, e.g. pricing source and verification date."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Create a new model under a provider. Model rows are pure data — no adapter change is required.

    Writes an `AIModelPriceComponent` row for every priced component in the same commit, so every
    model's price timeline starts at its own creation, never at the moment someone first edits
    its price.
    """
    t = get_t(request)
    model_name = model_name.strip()
    display_name = display_name.strip()
    notes = notes.strip()
    # Positional zip against COMPONENT_TYPES, not a hand-typed dict literal (code review finding)
    # — keeps this in lockstep with _price_form_fields' own COMPONENT_TYPES-driven mapping below,
    # instead of two independently-maintained lists of the same six component names.
    price_raw = dict(
        zip(COMPONENT_TYPES, (price_input, price_output, price_cache_read, price_cache_write, price_cache_write_5m, price_cache_write_1h))
    )
    form_state = {
        "provider_id": provider_id,
        "model_name": model_name,
        "display_name": display_name,
        "capability_tier": capability_tier,
        **_price_form_fields(price_raw),
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
        prices=price_raw,
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
        context_window_tokens=parsed["context_tokens"],
        max_output_tokens=parsed["max_tokens"],
        is_free=is_free,
        supports_web_search=supports_web_search,
        notes=notes or None,
    )
    db.add(model)
    db.flush()  # need model.id before the component rows can reference it
    for component_type, value in parsed["prices"].items():
        if value is not None:
            db.add(
                AIModelPriceComponent(
                    ai_model_id=model.id, component_type=component_type, price_per_unit_usd=value, changed_by_user_id=user.id
                )
            )
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)


@router.get("/{model_id}/edit")
def edit_ai_model_form(request: Request, model_id: int, db: Session = Depends(get_db)):
    """Render the model edit form, pre-filled with current values, including read-only created_at
    and its full price history (oldest changes at the bottom, each paired with the date range it
    was actually in effect).
    """
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
            "price_history": _price_history_rows(model),
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
    price_input: str = Form("", description="Price per 1M input tokens in USD. Leave empty if not yet known."),
    price_output: str = Form("", description="Price per 1M output tokens in USD. Leave empty if not yet known."),
    price_cache_read: str = Form("", description="Price per 1M cache-read tokens in USD. Leave empty if this provider doesn't bill it, or the price isn't yet known."),
    price_cache_write: str = Form("", description="Price per 1M cache-write tokens in USD (single-tier providers). Leave empty if this provider doesn't bill it, or the price isn't yet known."),
    price_cache_write_5m: str = Form("", description="Price per 1M cache-write tokens, 5-minute tier (Anthropic). Leave empty if not applicable."),
    price_cache_write_1h: str = Form("", description="Price per 1M cache-write tokens, 1-hour tier (Anthropic). Leave empty if not applicable."),
    context_window_tokens: str = Form("", description="Total context window in tokens."),
    max_output_tokens: str = Form("", description="Maximum output tokens per response."),
    is_free: bool = Form(False, description="Whether this model is genuinely free to call."),
    supports_web_search: bool = Form(False, description="Whether this model's adapter enables provider-side web search/grounding."),
    notes: str = Form("", description="Free-text admin notes."),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Update an existing model. `model_name` stays editable — unlike Prompt, a model has no run-time evidence tying to its exact text, only to its id (Run.model_id).

    Writes a new `AIModelPriceComponent` row only for a component whose submitted price actually
    differs from today's (`app.services.cost.prices_at`) — editing an unrelated field like
    `notes`, or resubmitting an unchanged price, never adds one.
    """
    t = get_t(request)
    model = _get_model_or_404(db, request, model_id)
    model_name = model_name.strip()
    display_name = display_name.strip()
    notes = notes.strip()
    # Positional zip against COMPONENT_TYPES — see create_ai_model's identical construction.
    price_raw = dict(
        zip(COMPONENT_TYPES, (price_input, price_output, price_cache_read, price_cache_write, price_cache_write_5m, price_cache_write_1h))
    )
    current_component_prices = prices_at(model.price_components, datetime.now(timezone.utc))

    parsed, error = _validate_and_parse_model_form(
        db,
        t,
        provider_id=provider_id,
        model_name=model_name,
        capability_tier=capability_tier,
        prices=price_raw,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        exclude_id=model_id,
        current_component_prices=current_component_prices,
    )

    if error:
        form_state = {
            "id": model_id,
            "provider_id": provider_id,
            "model_name": model_name,
            "display_name": display_name,
            "capability_tier": capability_tier,
            **_price_form_fields(price_raw),
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

    model.provider_id = provider_id
    model.model_name = model_name
    model.display_name = display_name or None
    model.capability_tier = capability_tier
    model.context_window_tokens = parsed["context_tokens"]
    model.max_output_tokens = parsed["max_tokens"]
    model.is_free = is_free
    model.supports_web_search = supports_web_search
    model.notes = notes or None

    for component_type, value in parsed["prices"].items():
        if value is not None and value != current_component_prices.get(component_type):
            db.add(
                AIModelPriceComponent(
                    ai_model_id=model.id, component_type=component_type, price_per_unit_usd=value, changed_by_user_id=user.id
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
            {"grouped_rows": _grouped_model_rows(_model_rows(db)), "error": t("errors.ai_model_in_use").format(count=run_count)},
            status_code=409,
        )
    db.delete(model)
    db.commit()
    return RedirectResponse(url="/ai-models", status_code=303)
