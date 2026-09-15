"""Run cost estimation (docs/TASKS_OPS_DASHBOARD.md T1, design decision 8).

Kept separate from app/services/dashboard.py — cost estimation is its own domain, useful to a
future billing engine independent of the client-facing dashboard.
"""

from app.models import AIModel

# The provider adapters (app/adapters/*.py) dump each provider SDK's usage object onto
# RawResponse.token_usage as-is, not through a shared shape — Anthropic and OpenAI happen to both
# use input_tokens/output_tokens, but Gemini's usage_metadata uses prompt_token_count/
# candidates_token_count instead (verified against real run data). Tried in this order.
_TOKEN_COUNT_KEYS = (
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

    for input_key, output_key in _TOKEN_COUNT_KEYS:
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
