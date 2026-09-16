"""Unit tests for app/services/cost.py (docs/TASKS_OPS_DASHBOARD.md T1; component-based pricing
rewrite in docs/TASKS_COST_COMPONENTS.md CC-4).

Pure-function tests — `AIModel` instances here are plain in-memory objects, never added to
`db_session` or committed, since `estimate_run_cost` only reads `model.is_free` plus the `prices`
dict passed in separately, and never needs a persisted row. Updated to the new
`estimate_run_cost(token_usage, model, prices)` signature only — new coverage for cache-component
pricing (versioning in time, double-counting, missing components) is CC-7's job, not this one's.
"""

from decimal import Decimal

from app.models import AIModel
from app.services.cost import estimate_run_cost


def _model(*, is_free: bool = False) -> AIModel:
    return AIModel(provider_id=1, model_name="test-model", capability_tier="standard", is_free=is_free)


def _prices(input_price: float, output_price: float) -> dict[str, Decimal]:
    return {"input": Decimal(str(input_price)), "output": Decimal(str(output_price))}


def test_estimate_run_cost_matches_run_35():
    # Real numbers from run 35 (gemini-3.1-flash-lite): 57 input + 1061 output tokens.
    model = _model()
    prices = _prices(0.25, 1.50)
    token_usage = {"prompt_token_count": 57, "candidates_token_count": 1061}

    cost = estimate_run_cost(token_usage, model, prices)

    assert cost == round(57 / 1e6 * 0.25 + 1061 / 1e6 * 1.50, 6)


def test_estimate_run_cost_handles_anthropic_openai_shaped_usage():
    # Anthropic and OpenAI both dump input_tokens/output_tokens, not Gemini's key names.
    model = _model()
    prices = _prices(3.0, 15.0)
    token_usage = {"input_tokens": 1000, "output_tokens": 500}

    cost = estimate_run_cost(token_usage, model, prices)

    assert cost == round(1000 / 1e6 * 3.0 + 500 / 1e6 * 15.0, 6)


def test_estimate_run_cost_is_none_without_token_usage():
    model = _model()

    assert estimate_run_cost(None, model, _prices(0.25, 1.50)) is None


def test_estimate_run_cost_is_none_without_model_price():
    token_usage = {"prompt_token_count": 57, "candidates_token_count": 1061}

    assert estimate_run_cost(token_usage, _model(), {"output": Decimal("1.50")}) is None
    assert estimate_run_cost(token_usage, _model(), {"input": Decimal("0.25")}) is None


def test_estimate_run_cost_is_none_for_unrecognized_usage_shape():
    model = _model()
    token_usage = {"some_other_provider_field": 123}

    assert estimate_run_cost(token_usage, model, _prices(0.25, 1.50)) is None


def test_estimate_run_cost_is_zero_for_a_free_model_regardless_of_price_or_usage():
    free_model = _model(is_free=True)

    assert estimate_run_cost({"input_tokens": 1000, "output_tokens": 500}, free_model, {}) == 0.0
    assert estimate_run_cost(None, free_model, {}) == 0.0
