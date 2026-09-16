"""Run cost estimation (docs/TASKS_OPS_DASHBOARD.md T1, design decision 8).

Kept separate from app/services/dashboard.py — cost estimation is its own domain, useful to a
future billing engine independent of the client-facing dashboard.
"""

from sqlalchemy import Float, case, cast, func
from sqlalchemy.sql.elements import ColumnElement

from app.models import AIModel

# The provider adapters (app/adapters/*.py) dump each provider SDK's usage object onto
# RawResponse.token_usage as-is, not through a shared shape — Anthropic and OpenAI happen to both
# use input_tokens/output_tokens, but Gemini's usage_metadata uses prompt_token_count/
# candidates_token_count instead (verified against real run data). Tried in this order. Public
# (not a leading-underscore module private) so app/services/ops_dashboard.py's SQL-side cost
# expression below builds from the exact same list — the two can never independently drift on
# which provider key names are recognized.
TOKEN_COUNT_KEY_PAIRS = (
    ("input_tokens", "output_tokens"),  # Anthropic, OpenAI
    ("prompt_token_count", "candidates_token_count"),  # Google Gemini
)


def estimate_run_cost(token_usage: dict | None, model: AIModel) -> float | None:
    """Estimate one run's cost in USD from its token usage and the model's per-1k-token prices.

    Returns `None` — never a silent `0.0` — when `token_usage` is missing, its shape doesn't match
    any known provider's usage keys, or the model is missing either price. The one exception is
    `model.is_free`: a model explicitly marked free costs 0.0 regardless of token usage, since that
    field exists precisely to distinguish "known to be free" from "price not entered yet" (same
    discipline as `RawResponse.has_citations`, see `AIModel`'s docstring) — treating it as unknown
    here would silently drop free-model runs out of any future cost aggregate instead of correctly
    counting them as zero-cost. This is an extension of design decision 8, not its literal wording.
    """
    if model.is_free:
        return 0.0

    if token_usage is None:
        return None
    if model.cost_per_1k_input_usd is None or model.cost_per_1k_output_usd is None:
        return None

    for input_key, output_key in TOKEN_COUNT_KEY_PAIRS:
        if input_key in token_usage and output_key in token_usage:
            input_tokens = token_usage[input_key]
            output_tokens = token_usage[output_key]
            if input_tokens is None or output_tokens is None:
                return None
            cost = (input_tokens / 1000) * float(model.cost_per_1k_input_usd) + (output_tokens / 1000) * float(
                model.cost_per_1k_output_usd
            )
            return round(cost, 6)

    return None


def run_cost_sql_expr(
    token_usage_col: ColumnElement,
    cost_per_1k_input_col: ColumnElement,
    cost_per_1k_output_col: ColumnElement,
    is_free_col: ColumnElement,
) -> ColumnElement:
    """SQL-side twin of `estimate_run_cost`, for use inside `SUM()`/`AVG()` aggregations
    (docs/TASKS_OPS_DASHBOARD.md T2, design decision 4 — aggregation always in SQL, never a Python
    loop over full `Run` rows summing per-row `estimate_run_cost()` calls).

    Builds one `COALESCE` chain per axis (input tokens, output tokens) across every key name in
    `TOKEN_COUNT_KEY_PAIRS`, rather than matching key pairs one at a time like the Python function
    does — safe because no known provider's raw usage payload mixes key names from another
    provider's shape, so the two axes can never end up combining mismatched values from different
    providers in practice.

    `is_free_col`, when true, always yields 0.0 regardless of the other columns — same is_free
    short-circuit `estimate_run_cost` applies, and for the same reason (see its docstring).
    """
    input_tokens_expr = func.coalesce(
        *(cast(token_usage_col[input_key].astext, Float) for input_key, _ in TOKEN_COUNT_KEY_PAIRS)
    )
    output_tokens_expr = func.coalesce(
        *(cast(token_usage_col[output_key].astext, Float) for _, output_key in TOKEN_COUNT_KEY_PAIRS)
    )
    cost_per_1k_input = cast(cost_per_1k_input_col, Float)
    cost_per_1k_output = cast(cost_per_1k_output_col, Float)

    return case(
        (is_free_col.is_(True), 0.0),
        (
            input_tokens_expr.is_(None)
            | output_tokens_expr.is_(None)
            | cost_per_1k_input_col.is_(None)
            | cost_per_1k_output_col.is_(None),
            None,
        ),
        else_=(input_tokens_expr / 1000.0) * cost_per_1k_input + (output_tokens_expr / 1000.0) * cost_per_1k_output,
    )
