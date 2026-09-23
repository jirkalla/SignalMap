"""Add xAI Grok provider + model row (sixth AI provider, docs/TASKS_NEW_PROVIDERS.md NP-T4).

One model seeded (`grok-4.7`), matching NP-T4's own scope — not a second "economy" tier, the same
call already made for Perplexity in migration 0033 (there's no second grok tier this task asked
for). `grok-4.7` confirmed real via `client.models.list()` against api.x.ai directly in NP-T1
(alongside `grok-4.3`/`4.5`/`4.6` and several out-of-scope `grok-4.20-*`/`grok-imagine-*`
variants) — resolves the "4.6 vs 4.7" naming conflict between an internal model list and xAI's own
docs in favor of what actually exists.

Pricing verified against docs.x.ai/docs/models, fetched 2026-09-23: xAI prices grok-4.7 in two
tiers depending on the REQUEST's own input length (under vs. at/above 200k tokens), not
time-of-day like DeepSeek — `ai_model_price_components` can't express a per-request threshold any
more than it can express DeepSeek's time-of-day one. **Confirmed with the user (2026-09-23): the
under-200k tier is seeded**, not the higher one — unlike DeepSeek's genuinely-unpredictable
peak/off-peak split, every real SignalMap prompt observed so far (a few hundred to ~12k tokens,
docs/TASKS_NEW_PROVIDERS.md NP-T1) sits far below the 200k threshold, so the lower tier is the
realistic price for actual usage, not an optimistic guess; the >200k tier would systematically
double-charge normal runs for a scenario this app essentially never hits.

  grok-4.7 (< 200k input tokens): input $2.00/1M, cached input $0.50/1M, output $6.00/1M

Context window 500,000 tokens (documented). `max_output_tokens` left `NULL` — not specified on
the pricing page, and this project doesn't invent numbers it can't cite a source for.

No `system_instruction_templates` row, same precedent as every other provider-seed migration.

`supports_web_search = TRUE` — the adapter (app/adapters/grok.py) always enables the Agent API's
`web_search` tool, and real geographic targeting via `user_location` was confirmed working
(module docstring, a raw HTTP probe that saw it echoed back in the response).

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-23

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_PRICING_SOURCE_NOTE = (
    "Pricing verified against docs.x.ai/docs/models on 2026-09-23. "
    "Under-200k-input tier seeded per user confirmation; the >=200k tier (2x) is never realistically hit by this app's prompts."
)


def upgrade() -> None:
    op.execute("INSERT INTO providers (code, name) VALUES ('xai', 'xAI Grok')")

    # bindparams, not an f-string, deliberately — _PRICING_SOURCE_NOTE contains an apostrophe
    # ("app's prompts"), which an f-string-interpolated single-quoted SQL literal (the pattern
    # migrations 0033/0034 used, safe only because their own notes happened to contain no
    # apostrophe) would break with a syntax error. Found by this migration failing app startup.
    op.execute(
        sa.text(
            """
            INSERT INTO ai_models (
                provider_id, model_name, display_name, capability_tier,
                context_window_tokens, max_output_tokens,
                supports_web_search, is_free, is_active, notes
            )
            VALUES
                (
                    (SELECT id FROM providers WHERE code = 'xai'),
                    'grok-4.7',
                    'Grok 4.7',
                    'standard',
                    500000,
                    NULL,
                    TRUE,
                    FALSE,
                    TRUE,
                    :notes
                )
            """
        ).bindparams(notes=_PRICING_SOURCE_NOTE)
    )

    op.execute(
        """
        INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
        SELECT id, 'input', 2.00, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'grok-4.7'
          AND provider_id = (SELECT id FROM providers WHERE code = 'xai')
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
        SELECT id, 'output', 6.00, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'grok-4.7'
          AND provider_id = (SELECT id FROM providers WHERE code = 'xai')
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
        SELECT id, 'cache_read', 0.50, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'grok-4.7'
          AND provider_id = (SELECT id FROM providers WHERE code = 'xai')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM ai_model_price_components
        WHERE ai_model_id IN (
            SELECT id FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'xai')
        )
        """
    )
    op.execute(
        "DELETE FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'xai')"
    )
    op.execute("DELETE FROM providers WHERE code = 'xai'")
