"""Unit tests for app/services/cost.py (docs/TASKS_OPS_DASHBOARD.md T1; component-based pricing
rewrite in docs/TASKS_COST_COMPONENTS.md CC-4; cache-component coverage added in CC-7).

Pure-function tests — `AIModel` instances here are plain in-memory objects, never added to
`db_session` or committed, since `estimate_run_cost` only reads `model.is_free` plus the `prices`
dict passed in separately, and never needs a persisted row. Price-versioning-in-time and the
Python/SQL parity guard both need a real database and the SQL twin, so they live in
tests/test_ops_dashboard.py instead (CC-7 steps 2-3).
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


def test_estimate_run_cost_is_zero_for_a_free_model_even_with_cache_components():
    # Regression: is_free short-circuits before any component logic runs at all, cache included.
    free_model = _model(is_free=True)
    token_usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 100},
    }

    assert estimate_run_cost(token_usage, free_model, {}) == 0.0


def test_estimate_run_cost_handles_ambiguous_plain_shape_unchanged():
    # A payload with only input_tokens/output_tokens and no Anthropic- or OpenAI-distinguishing
    # key can't be told apart from either provider (docs/TASKS_COST_COMPONENTS.md design decision
    # 13) — falls through to PLAIN_SHAPE, which has no cache paths to read, so it must price
    # exactly like the pre-CC-4 code always did.
    model = _model()
    prices = _prices(3.0, 15.0)
    token_usage = {"input_tokens": 1000, "output_tokens": 500}

    cost = estimate_run_cost(token_usage, model, prices)

    assert cost == round(1000 / 1e6 * 3.0 + 500 / 1e6 * 15.0, 6)


def test_estimate_run_cost_sums_anthropic_cache_read_and_both_write_tiers():
    model = _model()
    prices = {
        "input": Decimal("2.0"),
        "output": Decimal("10.0"),
        "cache_read": Decimal("0.20"),
        "cache_write_5m": Decimal("2.50"),
        "cache_write_1h": Decimal("4.0"),
    }
    token_usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 100},
    }

    cost = estimate_run_cost(token_usage, model, prices)

    expected = (
        1000 / 1e6 * 2.0  # input_tokens billed IN FULL — see the dedicated test below
        + 200 / 1e6 * 10.0
        + 500 / 1e6 * 0.20
        + 300 / 1e6 * 2.50
        + 100 / 1e6 * 4.0
    )
    assert cost == round(expected, 6)


def test_estimate_run_cost_does_not_reduce_anthropic_input_by_cache_read():
    # design decision 13: Anthropic's input_tokens EXCLUDES cache entirely, unlike OpenAI/Gemini —
    # so cache_read_input_tokens must never be subtracted from it. Cache-read tokens here equal
    # the full input_tokens count: if billable_input were wrongly reduced, it would go to 0 and
    # the input-priced term would vanish rather than showing up at full price.
    model = _model()
    prices = {"input": Decimal("2.0"), "output": Decimal("10.0"), "cache_read": Decimal("0.20")}
    token_usage = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 1000}

    cost = estimate_run_cost(token_usage, model, prices)

    expected = 1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0 + 1000 / 1e6 * 0.20
    assert cost == round(expected, 6)


def test_estimate_run_cost_does_not_double_count_openai_cached_tokens():
    # THE most important test in this module (docs/TASKS_COST_COMPONENTS.md CC-7): OpenAI's
    # input_tokens is a superset that already includes cached_tokens (and cache_write_tokens) —
    # design decision 13. 10000 input with 4000 cached must bill 6000 at full input price plus
    # 4000 at the cache-read price, never 10000 at full price plus 4000 again at cache price.
    model = _model()
    prices = {"input": Decimal("0.20"), "output": Decimal("1.20"), "cache_read": Decimal("0.02"), "cache_write": Decimal("0.25")}
    token_usage = {
        "input_tokens": 10_000,
        "output_tokens": 1_000,
        "input_tokens_details": {"cached_tokens": 4_000, "cache_write_tokens": 1_000},
    }

    cost = estimate_run_cost(token_usage, model, prices)

    # billable input = 10000 - 4000 (read) - 1000 (write) = 5000
    expected = 5_000 / 1e6 * 0.20 + 1_000 / 1e6 * 1.20 + 4_000 / 1e6 * 0.02 + 1_000 / 1e6 * 0.25
    assert cost == round(expected, 6)


def test_estimate_run_cost_does_not_double_count_gemini_cached_tokens():
    # Gemini's prompt_token_count likewise already includes cached_content_token_count.
    model = _model()
    prices = {"input": Decimal("1.50"), "output": Decimal("9.0"), "cache_read": Decimal("0.15")}
    token_usage = {"prompt_token_count": 2_000, "candidates_token_count": 300, "cached_content_token_count": 800}

    cost = estimate_run_cost(token_usage, model, prices)

    expected = 1_200 / 1e6 * 1.50 + 300 / 1e6 * 9.0 + 800 / 1e6 * 0.15
    assert cost == round(expected, 6)


def test_estimate_run_cost_is_none_for_nonzero_cache_component_with_no_price():
    # design decision 14: a nonzero cache component with no configured price is "unknown", never
    # silently priced as if it were free — the missing price must fail the whole run's cost, not
    # just be skipped.
    model = _model()
    token_usage = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 100}

    assert estimate_run_cost(token_usage, model, {"input": Decimal("2.0"), "output": Decimal("10.0")}) is None


def test_estimate_run_cost_prices_normally_when_unpriced_cache_component_is_zero():
    # The same missing price is fine when the component's own token count is zero — there's
    # nothing to price, so it's not "unknown", it's simply absent.
    model = _model()
    token_usage = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0}

    cost = estimate_run_cost(token_usage, model, {"input": Decimal("2.0"), "output": Decimal("10.0")})

    assert cost == round(1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0, 6)


def test_estimate_run_cost_does_not_double_count_perplexity_cache_creation_tokens():
    # NP-T2: Perplexity's input_tokens_details ALSO exists (like OpenAI's), but with different
    # nested field names (cache_creation_input_tokens, not cache_write_tokens) — must be detected
    # as PERPLEXITY_SHAPE, not misidentified as OPENAI_SHAPE, or these paths would read as None
    # and the cache tokens would double-bill exactly like the OpenAI regression test above guards.
    model = _model()
    prices = {"input": Decimal("0.25"), "output": Decimal("2.50"), "cache_read": Decimal("0.0625"), "cache_write": Decimal("0.10")}
    token_usage = {
        "input_tokens": 10_000,
        "output_tokens": 1_000,
        "input_tokens_details": {
            "cache_creation_input_tokens": 4_000,
            "cache_read_input_tokens": 1_000,
            "cached_tokens": 1_000,
        },
    }

    cost = estimate_run_cost(token_usage, model, prices)

    # billable input = 10000 - 1000 (read) - 4000 (write) = 5000
    expected = 5_000 / 1e6 * 0.25 + 1_000 / 1e6 * 2.50 + 1_000 / 1e6 * 0.0625 + 4_000 / 1e6 * 0.10
    assert cost == round(expected, 6)


def test_estimate_run_cost_handles_deepseek_shaped_usage():
    # NP-T3: DeepSeek uses prompt_tokens/completion_tokens (Chat Completions naming), distinct
    # from every other shape's key names — no collision, but must still be detected as its own
    # shape rather than falling through to None ("unrecognized").
    model = _model()
    prices = {"input": Decimal("0.30"), "output": Decimal("1.20")}
    token_usage = {"prompt_tokens": 95, "completion_tokens": 21, "total_tokens": 116, "prompt_cache_hit_tokens": 0}

    cost = estimate_run_cost(token_usage, model, prices)

    assert cost == round(95 / 1e6 * 0.30 + 21 / 1e6 * 1.20, 6)


def test_estimate_run_cost_does_not_double_count_deepseek_cache_hit_tokens():
    # prompt_tokens is the SUM of hit+miss (verified in NP-T1), same double-count trap as
    # OpenAI/Perplexity above.
    model = _model()
    prices = {"input": Decimal("1.32"), "output": Decimal("3.96"), "cache_read": Decimal("0.044")}
    token_usage = {
        "prompt_tokens": 10_000,
        "completion_tokens": 500,
        "prompt_cache_hit_tokens": 4_000,
        "prompt_cache_miss_tokens": 6_000,
    }

    cost = estimate_run_cost(token_usage, model, prices)

    # billable input = 10000 - 4000 (hit) = 6000
    expected = 6_000 / 1e6 * 1.32 + 500 / 1e6 * 3.96 + 4_000 / 1e6 * 0.044
    assert cost == round(expected, 6)


def test_estimate_run_cost_is_none_for_perplexity_nonzero_cache_creation_with_no_price():
    # design decision 14, Perplexity-specific: NP-T1's probe found no published cache-write price
    # for Perplexity, so migration 0033 seeds no cache_write component for it — a run that
    # actually reports nonzero cache_creation_input_tokens must come back "unknown", never free.
    model = _model()
    token_usage = {
        "input_tokens": 5_272,
        "output_tokens": 97,
        "input_tokens_details": {"cache_creation_input_tokens": 4_725, "cache_read_input_tokens": 0, "cached_tokens": 0},
    }

    assert estimate_run_cost(token_usage, model, {"input": Decimal("0.25"), "output": Decimal("2.50")}) is None
