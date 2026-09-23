"""Add DeepSeek provider + model rows (fifth AI provider, docs/TASKS_NEW_PROVIDERS.md NP-T3).

`supports_web_search = FALSE` for both models — not a placeholder pending a future tool, but a
confirmed structural fact (design decision 3): DeepSeek's API has no search/grounding surface at
all, so every run against it will have `has_citations = FALSE` permanently. This flag is what
`app/templates/runs/detail.html` and `app/services/dashboard.py`'s `_mention_visibility_base_query`
key off of to treat DeepSeek runs differently, without either place hardcoding the provider code
(design decision 8) — see those files' own comments.

Two models seeded, matching the economy/standard tier shape every other provider in this app
uses: `deepseek-flash` (economy) and `deepseek-v4-pro` (standard) — both confirmed real via
`client.models.list()` in NP-T1 (the documented `deepseek-v4-flash` does not exist there,
deprecated).

Pricing verified against api-docs.deepseek.com/quick_start/pricing, fetched 2026-09-23. DeepSeek
prices by time of day (peak: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri, excluding Chinese public
holidays — 2x the off-peak rate); `ai_model_price_components` has no way to express a
time-of-day-varying price (only `effective_from`-versioned history), so a single number had to be
chosen. **Confirmed with the user (2026-09-23): PEAK pricing is seeded** — the higher of the two
rates, so `/ops` cost estimates never silently understate real spend; an off-peak run will show as
more expensive here than it actually was, which is the accepted direction of error.

  deepseek-flash:  input (cache miss) $0.30/1M, cache read (hit) $0.006/1M, output $1.20/1M
  deepseek-v4-pro: input (cache miss) $1.32/1M, cache read (hit) $0.044/1M, output $3.96/1M

DeepSeek's own "cache miss" rate is exactly what `ai_model_price_components`'s `'input'`
component means here (the price billed on non-cached input tokens) and "cache hit" is exactly
`'cache_read'` — no reinterpretation needed, the concepts line up directly. No `cache_write`
component: DeepSeek's pricing has no write tier at all.

`context_window_tokens = 1_000_000` for both (documented). `max_output_tokens = 384000` for
deepseek-flash (documented); DeepSeek-v4-pro's is left `NULL` — not specified on the pricing page,
and this project doesn't invent numbers it can't cite a source for.

No `system_instruction_templates` row, same precedent as every other provider-seed migration
(0009 Anthropic, 0023 OpenAI, 0033 Perplexity) — falls back to
`DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE`.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-23

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_PRICING_SOURCE_NOTE = (
    "Pricing verified against api-docs.deepseek.com/quick_start/pricing on 2026-09-23. "
    "PEAK rate seeded (2x off-peak) per user confirmation, since time-of-day pricing has no schema representation."
)


def upgrade() -> None:
    op.execute("INSERT INTO providers (code, name) VALUES ('deepseek', 'DeepSeek')")

    op.execute(
        f"""
        INSERT INTO ai_models (
            provider_id, model_name, display_name, capability_tier,
            context_window_tokens, max_output_tokens,
            supports_web_search, is_free, is_active, notes
        )
        VALUES
            (
                (SELECT id FROM providers WHERE code = 'deepseek'),
                'deepseek-flash',
                'DeepSeek Flash',
                'economy',
                1000000,
                384000,
                FALSE,
                FALSE,
                TRUE,
                '{_PRICING_SOURCE_NOTE}'
            ),
            (
                (SELECT id FROM providers WHERE code = 'deepseek'),
                'deepseek-v4-pro',
                'DeepSeek V4 Pro',
                'standard',
                1000000,
                NULL,
                FALSE,
                FALSE,
                TRUE,
                '{_PRICING_SOURCE_NOTE}'
            )
        """
    )

    for model_name, input_price, output_price, cache_read_price in (
        ("deepseek-flash", 0.30, 1.20, 0.006),
        ("deepseek-v4-pro", 1.32, 3.96, 0.044),
    ):
        op.execute(
            sa.text(
                """
                INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
                SELECT id, 'input', :price, 'per_1m_tokens' FROM ai_models
                WHERE model_name = :model_name
                  AND provider_id = (SELECT id FROM providers WHERE code = 'deepseek')
                """
            ).bindparams(price=input_price, model_name=model_name)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
                SELECT id, 'output', :price, 'per_1m_tokens' FROM ai_models
                WHERE model_name = :model_name
                  AND provider_id = (SELECT id FROM providers WHERE code = 'deepseek')
                """
            ).bindparams(price=output_price, model_name=model_name)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
                SELECT id, 'cache_read', :price, 'per_1m_tokens' FROM ai_models
                WHERE model_name = :model_name
                  AND provider_id = (SELECT id FROM providers WHERE code = 'deepseek')
                """
            ).bindparams(price=cache_read_price, model_name=model_name)
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM ai_model_price_components
        WHERE ai_model_id IN (
            SELECT id FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'deepseek')
        )
        """
    )
    op.execute(
        "DELETE FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'deepseek')"
    )
    op.execute("DELETE FROM providers WHERE code = 'deepseek'")
