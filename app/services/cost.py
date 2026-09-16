"""Run cost estimation (docs/TASKS_OPS_DASHBOARD.md T1, design decision 8; rewritten to price from
`ai_model_price_components` in docs/TASKS_COST_COMPONENTS.md CC-4, design decisions 13 and 14).

Kept separate from app/services/dashboard.py — cost estimation is its own domain, useful to a
future billing engine independent of the client-facing dashboard.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import AIModel, AIModelPriceComponent
from app.models.provider import COMPONENT_TYPES


@dataclass(frozen=True)
class TokenUsageShape:
    """One provider's raw usage-payload shape, exactly as its adapter (app/adapters/*.py) dumps
    the provider SDK's usage object onto `RawResponse.token_usage` — see `TOKEN_USAGE_SHAPES`
    below for how each of these was derived from real stored data, not provider documentation.

    `cache_write_paths` maps `component_type` -> the JSON path to that tier's token count, so a
    provider with more than one write tier (Anthropic: 5-minute vs 1-hour) can report both.

    `input_includes_cache_read`/`input_includes_cache_write`: whether this provider's raw
    `input_key` count already contains the cache-read/cache-write tokens reported separately
    elsewhere in the payload (design decision 13). Anthropic reports both entirely outside
    `input_tokens`; OpenAI and Gemini fold cache-read into their input count — and, per the
    CC-4 finding below, OpenAI folds cache-write in too. Get either of these wrong and a cached
    provider's spend gets billed twice: once at full input price, once at the cache price.
    """

    input_key: str
    output_key: str
    cache_read_path: tuple[str, ...] | None
    cache_write_paths: dict[str, tuple[str, ...]]
    input_includes_cache_read: bool
    input_includes_cache_write: bool


# Verified against real data (docs/TASKS_COST_COMPONENTS.md CC-4 step 1), not provider docs — same
# discipline that caught Gemini's prompt_token_count key during T1 of the ops dashboard. Query run
# 2026-09-16 against every raw_responses.token_usage in the database:
#
#   SELECT p.code, jsonb_object_keys(rr.token_usage) AS key, count(*)
#   FROM raw_responses rr JOIN runs r ON r.id = rr.run_id JOIN ai_models m ON m.id = r.model_id
#   JOIN providers p ON p.id = m.provider_id GROUP BY 1, 2 ORDER BY 1, 2;
#
# anthropic:      cache_creation, cache_creation_input_tokens, cache_read_input_tokens,
#                 inference_geo, input_tokens, output_tokens, output_tokens_details,
#                 server_tool_use, service_tier
# google_gemini:  cached_content_token_count, cache_tokens_details, candidates_token_count,
#                 candidates_tokens_details, prompt_token_count, prompt_tokens_details,
#                 thoughts_token_count, tool_use_prompt_token_count,
#                 tool_use_prompt_tokens_details, total_token_count, traffic_type
# openai:         input_tokens, input_tokens_details, output_tokens, output_tokens_details,
#                 total_tokens
#
# All 20 Anthropic rows had all-zero cache fields (no cached traffic yet), so `cache_creation`'s
# nested shape (`{"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}`) and
# `input_tokens` excluding it entirely is documented behavior, not directly observed on nonzero
# data — matches the CC-4 task's own table exactly, no deviation.
#
# DEVIATION FROM THE CC-4 TASK'S TABLE, flagged and confirmed with the user before implementing:
# that table listed OpenAI's cache-write as "not billed separately in the report" (no mapping).
# The real data disagrees: `input_tokens_details` carries a live `cache_write_tokens` field (9 of
# 12 OpenAI rows nonzero, e.g. `{"cached_tokens": 0, "cache_write_tokens": 4432}`), and CC-3 had
# already primed OpenAI models with a real `cache_write` price ($0.25-$5/1M, pricier than input —
# a real premium tier, not a zero-cost bookkeeping field). Leaving it unmapped would silently
# under-price every OpenAI run that writes to cache — exactly what this whole CC-1..CC-8 effort
# exists to stop doing — so `cache_write_paths["cache_write"]` maps it here instead.
# `input_includes_cache_write=True` for OpenAI is derived, not assumed: across all 12 OpenAI rows,
# `total_tokens == input_tokens + output_tokens` held exactly, with no separate addition for
# `cache_write_tokens` — so `cache_write_tokens` (like `cached_tokens`) is a *subset* of
# `input_tokens`, not additional to it. Confirmed on run ids 58/60/61/62/63/74/77/80/82/89/91/99.
GEMINI_SHAPE = TokenUsageShape(
    input_key="prompt_token_count",
    output_key="candidates_token_count",
    cache_read_path=("cached_content_token_count",),
    cache_write_paths={},
    input_includes_cache_read=True,
    input_includes_cache_write=False,
)

OPENAI_SHAPE = TokenUsageShape(
    input_key="input_tokens",
    output_key="output_tokens",
    cache_read_path=("input_tokens_details", "cached_tokens"),
    cache_write_paths={"cache_write": ("input_tokens_details", "cache_write_tokens")},
    input_includes_cache_read=True,
    input_includes_cache_write=True,
)

ANTHROPIC_SHAPE = TokenUsageShape(
    input_key="input_tokens",
    output_key="output_tokens",
    cache_read_path=("cache_read_input_tokens",),
    cache_write_paths={
        "cache_write_5m": ("cache_creation", "ephemeral_5m_input_tokens"),
        "cache_write_1h": ("cache_creation", "ephemeral_1h_input_tokens"),
    },
    input_includes_cache_read=False,
    input_includes_cache_write=False,
)

# Anthropic and OpenAI happen to share input_tokens/output_tokens key names, so a payload with
# neither provider's distinguishing key (no cache activity ever recorded) can't be told apart from
# either by key name alone — and doesn't need to be: with no cache fields present, both providers'
# cache handling is a no-op anyway, so this shared fallback prices it exactly like the pre-CC-4
# code did. Its own `input_includes_cache_read`/`_write` are moot (no cache_read_path/
# cache_write_paths to ever read from).
PLAIN_SHAPE = TokenUsageShape(
    input_key="input_tokens",
    output_key="output_tokens",
    cache_read_path=None,
    cache_write_paths={},
    input_includes_cache_read=False,
    input_includes_cache_write=False,
)

# Public (not a leading-underscore module private), same reason TOKEN_COUNT_KEY_PAIRS was public
# before it: run_cost_sql_expr below builds its per-axis COALESCE chains from this exact tuple, so
# the Python and SQL implementations can never independently drift on which keys/paths they know.
TOKEN_USAGE_SHAPES = (GEMINI_SHAPE, OPENAI_SHAPE, ANTHROPIC_SHAPE, PLAIN_SHAPE)


def _select_shape(token_usage: dict) -> TokenUsageShape | None:
    """Pick the provider shape a payload matches, by its most distinguishing key — never by
    `model.provider`, since `estimate_run_cost` is a pure function with no access to that relation
    (tests build `AIModel` rows with no session, see this module's test file's header) and the SQL
    twin below can't cheaply join `providers` into every cost aggregation either.

    Order matters (docs/TASKS_COST_COMPONENTS.md CC-4 step 3): Anthropic and OpenAI both use
    input_tokens/output_tokens, so the two are told apart by which OTHER key is present, checked
    before ever falling through to the shared plain shape.
    """
    if "prompt_token_count" in token_usage:
        return GEMINI_SHAPE
    if "input_tokens_details" in token_usage:
        return OPENAI_SHAPE
    if "cache_creation" in token_usage or "cache_read_input_tokens" in token_usage:
        return ANTHROPIC_SHAPE
    if "input_tokens" in token_usage and "output_tokens" in token_usage:
        return PLAIN_SHAPE
    return None


def _read_path(payload: dict, path: tuple[str, ...] | None) -> float | None:
    """Walk a nested-key path into a JSON-shaped dict, `None` if any step is missing."""
    if path is None:
        return None
    value: object = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value  # type: ignore[return-value]


def estimate_run_cost(token_usage: dict | None, model: AIModel, prices: dict[str, Decimal]) -> float | None:
    """Estimate one run's cost in USD from its token usage and `prices` (component_type -> USD per
    1M tokens, from `prices_at`/`current_prices` — the price effective at the run's own time, not
    necessarily today's).

    Returns `None` — never a silent underestimate — when `token_usage` is missing, its shape
    doesn't match any known provider's usage keys, the model is missing an `input`/`output` price,
    or a component reports a NONZERO token count with no matching price in `prices` (design
    decision 14: "no price" means "unknown", never "free"; a zero count with no price is fine —
    there's nothing to price). The one exception is `model.is_free`: explicitly free costs 0.0
    regardless of usage or prices, same as before CC-4 (see `AIModel`'s docstring on why that flag
    exists).
    """
    if model.is_free:
        return 0.0
    if token_usage is None:
        return None

    shape = _select_shape(token_usage)
    if shape is None:
        return None

    input_tokens = token_usage.get(shape.input_key)
    output_tokens = token_usage.get(shape.output_key)
    if input_tokens is None or output_tokens is None:
        return None
    if "input" not in prices or "output" not in prices:
        return None

    cache_read_tokens = _read_path(token_usage, shape.cache_read_path) or 0
    write_tokens_by_type = {
        component_type: (_read_path(token_usage, path) or 0) for component_type, path in shape.cache_write_paths.items()
    }

    billable_input = input_tokens
    if shape.input_includes_cache_read:
        billable_input -= cache_read_tokens
    if shape.input_includes_cache_write:
        billable_input -= sum(write_tokens_by_type.values())
    billable_input = max(0, billable_input)

    cost = (billable_input / 1e6) * float(prices["input"]) + (output_tokens / 1e6) * float(prices["output"])

    if cache_read_tokens:
        if "cache_read" not in prices:
            return None
        cost += (cache_read_tokens / 1e6) * float(prices["cache_read"])

    for component_type, tokens in write_tokens_by_type.items():
        if tokens:
            if component_type not in prices:
                return None
            cost += (tokens / 1e6) * float(prices[component_type])

    return round(cost, 6)


def _json_path_expr(col: ColumnElement, path: tuple[str, ...]) -> ColumnElement:
    """SQL equivalent of `_read_path`: chain JSONB `->` down to the last key, `->>` (as text) on
    it, cast to Float — NULL (not an error) at any missing step, same as the Python version.
    """
    expr = col
    for key in path[:-1]:
        expr = expr[key]
    return cast(expr[path[-1]].astext, Float)


def run_cost_sql_expr(
    token_usage_col: ColumnElement,
    model_id_col: ColumnElement,
    started_at_col: ColumnElement,
    is_free_col: ColumnElement,
) -> ColumnElement:
    """SQL-side twin of `estimate_run_cost`, for use inside `SUM()`/`AVG()` aggregations
    (docs/TASKS_OPS_DASHBOARD.md T2, design decision 4 — aggregation always in SQL, never a Python
    loop over full `Run` rows).

    Prices come from correlated scalar subqueries against `ai_model_price_components`, one per
    `component_type`, each picking the latest row with `effective_from <= started_at_col` — the
    run's OWN time, not today's price (docs/TASKS_COST_COMPONENTS.md design decision 4) — via
    exactly the `(ai_model_id, component_type, effective_from DESC)` shape
    `idx_price_components_lookup` (migration 0025) was built for.

    Anthropic and OpenAI can't be told apart by `token_usage_col`'s `input_tokens`/`output_tokens`
    key names alone (both use them), so which axis subtracts cache tokens from "billable input" is
    decided per-row from whichever of OpenAI's `input_tokens_details` or Anthropic's
    `cache_creation`/`cache_read_input_tokens` the row's own JSON actually has — not from a
    provider join, for the same reason `_select_shape` avoids one (see its docstring). A row with
    neither (Gemini, or a cache-less Anthropic/OpenAI/plain payload) is treated as "cache read is
    included in input" by default, which is correct for Gemini and a no-op for everyone else with
    no cache tokens to subtract anyway.
    """

    def price_subquery(component_type: str) -> ColumnElement:
        c = AIModelPriceComponent
        return (
            select(c.price_per_unit_usd)
            .where(c.ai_model_id == model_id_col, c.component_type == component_type, c.effective_from <= started_at_col)
            .order_by(c.effective_from.desc())
            .limit(1)
            .scalar_subquery()
        )

    prices = {component_type: price_subquery(component_type) for component_type in COMPONENT_TYPES}

    unique_io_pairs = dict.fromkeys((shape.input_key, shape.output_key) for shape in TOKEN_USAGE_SHAPES)
    input_tokens_expr = func.coalesce(*(cast(token_usage_col[input_key].astext, Float) for input_key, _ in unique_io_pairs))
    output_tokens_expr = func.coalesce(*(cast(token_usage_col[output_key].astext, Float) for _, output_key in unique_io_pairs))

    # Cache-component expressions, built generically from TOKEN_USAGE_SHAPES (code review finding
    # — same discipline as run_token_sql_expr's axes below) rather than one hand-named local per
    # provider/tier: a future shape's cache paths are picked up automatically here instead of
    # needing a matching hand-edit that's easy to forget (the old per-provider-local version could
    # silently under-price a new provider's cache tokens if someone added it to TOKEN_USAGE_SHAPES
    # without also remembering to wire it into this function by hand).
    cache_read_paths = dict.fromkeys(shape.cache_read_path for shape in TOKEN_USAGE_SHAPES if shape.cache_read_path)
    cache_token_exprs: dict[str, ColumnElement] = {
        "cache_read": func.coalesce(*(_json_path_expr(token_usage_col, p) for p in cache_read_paths))
    }
    write_component_types = dict.fromkeys(ct for shape in TOKEN_USAGE_SHAPES for ct in shape.cache_write_paths)
    for component_type in write_component_types:
        write_paths = [shape.cache_write_paths[component_type] for shape in TOKEN_USAGE_SHAPES if component_type in shape.cache_write_paths]
        cache_token_exprs[component_type] = func.coalesce(*(_json_path_expr(token_usage_col, p) for p in write_paths))

    # Anthropic's cache tokens are never inside input_tokens, at any tier (design decision 13) —
    # detected from presence of any Anthropic-specific cache path, since Anthropic and OpenAI/plain
    # share the same input_tokens/output_tokens key names and can't be told apart from those alone
    # (same reasoning _select_shape's docstring gives for checking OTHER keys first).
    is_anthropic_shape = (
        _json_path_expr(token_usage_col, ANTHROPIC_SHAPE.cache_read_path).is_not(None)
        | _json_path_expr(token_usage_col, ANTHROPIC_SHAPE.cache_write_paths["cache_write_5m"]).is_not(None)
        | _json_path_expr(token_usage_col, ANTHROPIC_SHAPE.cache_write_paths["cache_write_1h"]).is_not(None)
    )

    # Anthropic's cache tokens are never inside input_tokens, at any tier — subtract nothing for
    # it. Everyone else (Gemini's cache_read, OpenAI's cache_read + cache_write) has them folded
    # in, so subtract whichever of those this row actually reports (0 where absent). "cache_write"
    # here is deliberately only OpenAI's tier (the only shape besides Anthropic's that defines
    # one) — Anthropic's own cache_write_5m/cache_write_1h are handled by the is_anthropic_shape
    # branch above, not this one.
    non_anthropic_subtraction = func.coalesce(cache_token_exprs["cache_read"], 0.0) + func.coalesce(
        cache_token_exprs["cache_write"], 0.0
    )
    billable_input_expr = func.greatest(
        0.0, input_tokens_expr - case((is_anthropic_shape, 0.0), else_=non_anthropic_subtraction)
    )

    missing_price_conditions = [
        (func.coalesce(token_expr, 0.0) != 0.0) & prices[component_type].is_(None)
        for component_type, token_expr in cache_token_exprs.items()
    ]
    any_missing_cache_price = missing_price_conditions[0]
    for condition in missing_price_conditions[1:]:
        any_missing_cache_price = any_missing_cache_price | condition

    cache_cost_terms = [
        func.coalesce(token_expr, 0.0) / 1_000_000.0 * func.coalesce(cast(prices[component_type], Float), 0.0)
        for component_type, token_expr in cache_token_exprs.items()
    ]
    cache_cost = cache_cost_terms[0]
    for term in cache_cost_terms[1:]:
        cache_cost = cache_cost + term

    input_price = cast(prices["input"], Float)
    output_price = cast(prices["output"], Float)

    return case(
        (is_free_col.is_(True), 0.0),
        (
            input_tokens_expr.is_(None) | output_tokens_expr.is_(None) | prices["input"].is_(None) | prices["output"].is_(None),
            None,
        ),
        (any_missing_cache_price, None),
        else_=(billable_input_expr / 1_000_000.0) * input_price + (output_tokens_expr / 1_000_000.0) * output_price + cache_cost,
    )


def raw_input_output_tokens(token_usage: dict | None) -> tuple[int | None, int | None]:
    """The input/output token counts a run's payload reported, exactly as the provider reported
    them — never netted against cache (design decision 7: tokens shown in the UI are the
    provider's own raw figures; subtracting cached tokens is a *pricing* concern, handled inside
    `estimate_run_cost`'s `billable_input`, not a *display* one). `(None, None)` when there's no
    payload or its shape isn't recognized — same shape selection `estimate_run_cost` uses.

    Used by `app.services.ops_dashboard.recent_runs`, which needs these per-row in Python (a
    bounded ~15-row fetch, not the full-table aggregation `run_token_sql_expr` below backs).
    """
    if token_usage is None:
        return None, None
    shape = _select_shape(token_usage)
    if shape is None:
        return None, None
    return token_usage.get(shape.input_key), token_usage.get(shape.output_key)


def run_token_sql_expr(token_usage_col: ColumnElement, axis: str) -> ColumnElement:
    """SQL-side raw token count for one axis (`"input"`, `"output"`, `"cache_read"`,
    `"cache_write"` — the last summing every write tier, Anthropic's 5m + 1h included), for use
    inside `SUM()` aggregations — the token-counting sibling of `run_cost_sql_expr`, built from the
    exact same `TOKEN_USAGE_SHAPES` so the two can never independently drift on which keys/paths
    they recognize.

    Always the RAW reported count, never `run_cost_sql_expr`'s `billable_input` (design decision 7
    — same reasoning as `raw_input_output_tokens` above).

    Two different "missing" cases, two different results, on purpose:
    - `cache_read`/`cache_write` axes default a missing field to 0, not NULL: "how many
      cache-write tokens did this run have" always has an answer once the payload's shape is
      recognized at all (0 tokens is a real answer, not an unknown one — a shape that has no
      cache activity this call is not the same as a shape that couldn't be identified).
    - `input`/`output` axes do NOT default to 0: every recognized shape always reports an input
      and an output key, so a NULL result there means the payload matched NO known shape at
      all — that must propagate as "unknown" (same as `run_cost_sql_expr`'s `input_tokens_expr`/
      `output_tokens_expr` and the `raw_input_output_tokens` Python sibling), not be masked as
      "0 tokens" the way an unrelated field genuinely being absent would be.

    Separately, a row with NO payload at all (`token_usage_col IS NULL`, no `RawResponse`) still
    yields SQL NULL here regardless of axis — so `SUM()` over a whole scope only comes back NULL
    when literally no run in scope has a `raw_responses` row, exactly the distinction
    `OpsSummary.total_input_tokens` etc. document.
    """
    if axis == "input":
        # No trailing 0.0 default here (unlike cache_read/cache_write below): every known shape
        # always has an input_key, so a NULL result means the payload matched NO shape at all —
        # that must propagate as "unknown", not be masked as "0 tokens" (code review finding,
        # matches run_cost_sql_expr's input_tokens_expr and the raw_input_output_tokens Python
        # sibling, both of which already treat an unrecognized shape as unknown, not zero).
        paths = dict.fromkeys((shape.input_key,) for shape in TOKEN_USAGE_SHAPES)
        value = func.coalesce(*(_json_path_expr(token_usage_col, p) for p in paths))
    elif axis == "output":
        paths = dict.fromkeys((shape.output_key,) for shape in TOKEN_USAGE_SHAPES)
        value = func.coalesce(*(_json_path_expr(token_usage_col, p) for p in paths))
    elif axis == "cache_read":
        paths = dict.fromkeys(shape.cache_read_path for shape in TOKEN_USAGE_SHAPES if shape.cache_read_path)
        value = func.coalesce(*(_json_path_expr(token_usage_col, p) for p in paths), 0.0)
    elif axis == "cache_write":
        write_paths = [path for shape in TOKEN_USAGE_SHAPES for path in shape.cache_write_paths.values()]
        terms = [func.coalesce(_json_path_expr(token_usage_col, path), 0.0) for path in write_paths]
        value = terms[0]
        for term in terms[1:]:
            value = value + term
    else:
        raise ValueError(f"unknown token axis: {axis!r}")

    return case((token_usage_col.is_(None), None), else_=value)


def load_price_components(db: Session, model_ids: list[int]) -> dict[int, list[AIModelPriceComponent]]:
    """Every `AIModelPriceComponent` row for the given models, one query, newest-first per model.

    Bulk-loads across every model in `model_ids` in a single round trip rather than one query per
    model — same discipline as `app/routers/ai_models.py`'s `_model_rows`, which pulls run counts
    for every model with one `GROUP BY` instead of a query per row.
    """
    if not model_ids:
        return {}

    rows = db.scalars(
        select(AIModelPriceComponent)
        .where(AIModelPriceComponent.ai_model_id.in_(model_ids))
        .order_by(AIModelPriceComponent.effective_from.desc())
    ).all()

    by_model: dict[int, list[AIModelPriceComponent]] = defaultdict(list)
    for row in rows:
        by_model[row.ai_model_id].append(row)
    return dict(by_model)


def prices_at(components: list[AIModelPriceComponent], at: datetime) -> dict[str, Decimal]:
    """The price of each component type effective as of `at`, from an already-loaded list.

    Pure function over data already in hand, no DB access — same shape as `estimate_run_cost`.
    For each `component_type`, keeps the row with the latest `effective_from <= at`; a component
    type with no row effective by `at` is simply absent from the result, never defaulted to zero
    (docs/TASKS_COST_COMPONENTS.md design decision 14 — "no row" means "price unknown"). Input
    order doesn't matter; every row is considered.
    """
    latest_by_type: dict[str, AIModelPriceComponent] = {}
    for component in components:
        if component.effective_from > at:
            continue
        current = latest_by_type.get(component.component_type)
        if current is None or component.effective_from > current.effective_from:
            latest_by_type[component.component_type] = component
    return {component_type: row.price_per_unit_usd for component_type, row in latest_by_type.items()}


def current_prices(db: Session, model_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    """Each model's component prices effective right now — what the admin UI (CC-3) shows as

    "today's price" per model. Every id in `model_ids` is a key in the result, even a model with
    no price components at all (mapped to `{}`), so callers can index without a `.get(..., {})`.
    """
    components_by_model = load_price_components(db, model_ids)
    now = datetime.now(timezone.utc)
    return {model_id: prices_at(components_by_model.get(model_id, []), now) for model_id in model_ids}
