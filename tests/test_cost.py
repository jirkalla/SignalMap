"""Unit tests for app/services/cost.py (docs/TASKS_OPS_DASHBOARD.md T1).

Pure-function tests — `AIModel` instances here are plain in-memory objects, never added to
`db_session` or committed, since `estimate_run_cost` only reads the two price columns and never
needs a persisted row.
"""

from app.models import AIModel
from app.services.cost import estimate_run_cost


def _model(*, cost_in: float | None, cost_out: float | None, is_free: bool = False) -> AIModel:
    return AIModel(
        provider_id=1,
        model_name="test-model",
        capability_tier="standard",
        cost_per_1k_input_usd=cost_in,
        cost_per_1k_output_usd=cost_out,
        is_free=is_free,
    )


def test_estimate_run_cost_matches_run_35():
    # Real numbers from run 35 (gemini-3.1-flash-lite): 57 input + 1061 output tokens.
    model = _model(cost_in=0.00025, cost_out=0.00150)
    token_usage = {"prompt_token_count": 57, "candidates_token_count": 1061}

    cost = estimate_run_cost(token_usage, model)

    assert cost == round(57 / 1000 * 0.00025 + 1061 / 1000 * 0.00150, 6)


def test_estimate_run_cost_handles_anthropic_openai_shaped_usage():
    # Anthropic and OpenAI both dump input_tokens/output_tokens, not Gemini's key names.
    model = _model(cost_in=0.003, cost_out=0.015)
    token_usage = {"input_tokens": 1000, "output_tokens": 500}

    cost = estimate_run_cost(token_usage, model)

    assert cost == round(1000 / 1000 * 0.003 + 500 / 1000 * 0.015, 6)


def test_estimate_run_cost_is_none_without_token_usage():
    model = _model(cost_in=0.00025, cost_out=0.00150)

    assert estimate_run_cost(None, model) is None


def test_estimate_run_cost_is_none_without_model_price():
    token_usage = {"prompt_token_count": 57, "candidates_token_count": 1061}

    assert estimate_run_cost(token_usage, _model(cost_in=None, cost_out=0.00150)) is None
    assert estimate_run_cost(token_usage, _model(cost_in=0.00025, cost_out=None)) is None


def test_estimate_run_cost_is_none_for_unrecognized_usage_shape():
    model = _model(cost_in=0.00025, cost_out=0.00150)
    token_usage = {"some_other_provider_field": 123}

    assert estimate_run_cost(token_usage, model) is None


def test_estimate_run_cost_is_zero_for_a_free_model_regardless_of_price_or_usage():
    free_model = _model(cost_in=None, cost_out=None, is_free=True)

    assert estimate_run_cost({"input_tokens": 1000, "output_tokens": 500}, free_model) == 0.0
    assert estimate_run_cost(None, free_model) == 0.0
