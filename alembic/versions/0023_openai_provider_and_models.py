"""Add OpenAI provider + model rows (third AI provider, docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T6).

Model names, context/max-output token counts, and pricing are copied from OpenAI's own docs
(developers.openai.com/api/docs/pricing and .../api/docs/models/gpt-5.6-sol, fetched
2026-09-13) rather than guessed — same discipline as migration 0009's Anthropic seed. Seeds the
three tiers of the current GPT-5.6 generation (Sol/Terra/Luna), the same one-generation/
three-tier shape Anthropic's Haiku/Sonnet/Opus seed already uses, rather than mixing in the
separate, pricier GPT-6 Astra tier.

`cost_per_1k_input_usd`/`cost_per_1k_output_usd` are the per-1M prices divided by 1000, per the
project's existing storage convention (see app/routers/ai_models.py's `_parse_price`).
`supports_web_search = TRUE` — the OpenAI adapter (CPH-T7) uses the Responses API `web_search`
tool. `is_free = FALSE` (OpenAI has no free tier for these models).

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_PRICING_SOURCE_NOTE = "Pricing verified against developers.openai.com/api/docs/pricing on 2026-09-13."


def upgrade() -> None:
    op.execute("INSERT INTO providers (code, name) VALUES ('openai', 'OpenAI ChatGPT')")

    op.execute(
        f"""
        INSERT INTO ai_models (
            provider_id, model_name, display_name, capability_tier,
            cost_per_1k_input_usd, cost_per_1k_output_usd,
            context_window_tokens, max_output_tokens,
            supports_web_search, is_free, is_active, notes
        )
        VALUES
            (
                (SELECT id FROM providers WHERE code = 'openai'),
                'gpt-5.6-sol',
                'GPT-5.6 Sol',
                'flagship',
                0.004,
                0.020,
                1050000,
                128000,
                TRUE,
                FALSE,
                TRUE,
                '{_PRICING_SOURCE_NOTE}'
            ),
            (
                (SELECT id FROM providers WHERE code = 'openai'),
                'gpt-5.6-terra',
                'GPT-5.6 Terra',
                'standard',
                0.002,
                0.012,
                1050000,
                128000,
                TRUE,
                FALSE,
                TRUE,
                '{_PRICING_SOURCE_NOTE}'
            ),
            (
                (SELECT id FROM providers WHERE code = 'openai'),
                'gpt-5.6-luna',
                'GPT-5.6 Luna',
                'economy',
                0.0002,
                0.0012,
                1050000,
                128000,
                TRUE,
                FALSE,
                TRUE,
                '{_PRICING_SOURCE_NOTE}'
            )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'openai')"
    )
    op.execute("DELETE FROM providers WHERE code = 'openai'")
