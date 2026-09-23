"""Add Perplexity provider + model row (fourth AI provider, docs/TASKS_NEW_PROVIDERS.md NP-T2).

`model_name` is the full `{vendor}/{model}` string the Agent API's `model` field expects
(`perplexity/sonar`), not a bare model name — see docs/TASKS_NEW_PROVIDERS.md's "Verified response
shapes (NP-T1)" section for why: the Agent API is a router across several vendors' models
(`client.models.list()` also returned e.g. `xai/grok-4.7`, `anthropic/claude-opus-5-5`), and only
one entry in that catalog is actually Perplexity's own grounded answer model — the rest are
third-party open models Perplexity happens to host. Only that one (`perplexity/sonar`) is seeded
here; this migration is about the Perplexity provider surface, not a cross-vendor routing catalog.

Pricing verified against docs.perplexity.ai/getting-started/pricing, fetched 2026-09-23: input
$0.25/1M, output $2.50/1M, cache read $0.0625/1M. **No cache-write/cache-creation price is
seeded** — the pricing page explicitly does not specify one ("Cache write/creation rates are not
specified"). Per docs/TASKS_COST_COMPONENTS.md design decision 14 ("no price" means "unknown",
never "free"), `estimate_run_cost` (app/services/cost.py) already handles this correctly: a run
whose payload reports nonzero `cache_creation_input_tokens` (PERPLEXITY_SHAPE's `cache_write`
component) with no matching price row returns `None` (cost unknown) rather than silently pricing
it as free or guessing a number this migration has no source for.

`context_window_tokens = 128000`, verified against docs.perplexity.ai/docs/sonar/models/sonar
("128K context length"), fetched 2026-09-23 — that page describes the legacy Sonar Chat
Completions model being retired into the Agent API (design decision 1), so its architecture
(and context window) is the same underlying model `perplexity/sonar` now runs as; its pricing
is NOT reused here, though (that page quotes $1/1M output, the legacy rate, vs. the $2.50/1M
Agent API rate actually seeded below — exactly the kind of stale-doc trap design decision 1
warns about). `max_output_tokens` stays `NULL` (the column is nullable) — not published anywhere
found, and this project doesn't invent numbers it can't cite a source for (same discipline as
`_PRICING_SOURCE_NOTE` below).

Only one model row: confirmed with the user (2026-09-23) that Perplexity's Agent API has exactly
one Perplexity-branded model (`sonar`) — no second "economy" tier exists to seed, unlike
Anthropic/OpenAI/Gemini's real multi-tier generations. The other `perplexity/`-prefixed catalog
entries (`glm-5.3`, `kimi-k3`, `kimi-k2.7-code`, `nemotron-3-ultra-550b-a55b`) are third-party
open models Perplexity hosts, not eligible substitutes for a "Perplexity economy tier" — adding
one of them here as a fake second tier was considered and explicitly declined.

No `system_instruction_templates` row: neither the Anthropic (migration 0009) nor the OpenAI
(migration 0023) provider-seed migration added one either, and `app/services/run_execution.py`'s
`_build_system_instruction` already falls back to `DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE` for any
provider with no row — same behavior Perplexity gets here, by the same precedent, not an
oversight.

`supports_web_search = TRUE` — the adapter (app/adapters/perplexity.py) always enables the
Agent API's `web_search` tool. `is_free = FALSE`.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-23

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_PRICING_SOURCE_NOTE = "Pricing verified against docs.perplexity.ai/getting-started/pricing on 2026-09-23. No cache-write price published."


def upgrade() -> None:
    op.execute("INSERT INTO providers (code, name) VALUES ('perplexity', 'Perplexity')")

    # bindparams, not an f-string — _PRICING_SOURCE_NOTE is plain text today, but interpolating
    # it directly into a single-quoted SQL literal breaks the moment it ever contains an
    # apostrophe (found in migration 0035, which fixed this same pattern for itself; backported
    # here so 0033 doesn't carry the identical latent bug).
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
                    (SELECT id FROM providers WHERE code = 'perplexity'),
                    'perplexity/sonar',
                    'Perplexity Sonar',
                    'standard',
                    128000,
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
        SELECT id, 'input', 0.25, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'perplexity/sonar'
          AND provider_id = (SELECT id FROM providers WHERE code = 'perplexity')
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
        SELECT id, 'output', 2.50, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'perplexity/sonar'
          AND provider_id = (SELECT id FROM providers WHERE code = 'perplexity')
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_price_components (ai_model_id, component_type, price_per_unit_usd, unit)
        SELECT id, 'cache_read', 0.0625, 'per_1m_tokens' FROM ai_models
        WHERE model_name = 'perplexity/sonar'
          AND provider_id = (SELECT id FROM providers WHERE code = 'perplexity')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM ai_model_price_components
        WHERE ai_model_id IN (
            SELECT id FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'perplexity')
        )
        """
    )
    op.execute(
        "DELETE FROM ai_models WHERE provider_id = (SELECT id FROM providers WHERE code = 'perplexity')"
    )
    op.execute("DELETE FROM providers WHERE code = 'perplexity'")
