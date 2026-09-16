"""Add ai_model_price_components: per-component, time-versioned model pricing.

`estimate_run_cost` (`app/services/cost.py`) only ever priced input/output tokens, which
undercounted real spend once cache tiers entered the picture — Anthropic bills cache read plus
two separate cache-write tiers (5m/1h ephemeral), and Gemini/OpenAI each bill one cache-read
tier, none of which `ai_models.cost_per_1k_input_usd`/`cost_per_1k_output_usd` (2 fixed columns)
can express. This table replaces that flat pair with a normalized, versioned list of priced
components per model — same append-only-history principle as `ai_model_price_history`
(migration 0020), just extended from 2 fixed columns to an open `component_type` set (docs/
TASKS_COST_COMPONENTS.md CC-1, design decisions 1-4).

Purely additive: `ai_models.cost_per_1k_*_usd` and `ai_model_price_history` are untouched and
still read by the app exactly as before. Dropping them is deliberately deferred to CC-1's last
sibling task (migration 0026, design decision 12) so the app keeps starting cleanly at every
intermediate step instead of going unbootable between migrations.

Price is stored per 1M tokens in NUMERIC(12,6), not per 1k in NUMERIC(10,5) like the column it
replaces (design decision 11): Gemini 3.1 flash-lite's cache-read price is $0.025/1M, i.e.
0.000025/1k, which the old per-1k NUMERIC(10,5) column would round up to 0.00003 — a 20% error
on the very first new component. Per-1M is also the unit providers publish prices in, so this
removes the per-1k/per-1M conversions `app/routers/ai_models.py` does today rather than adding
new ones.

`unit`'s CHECK already allows `'per_call'` even though every component this migration and its
CC-1..CC-8 siblings write is `'per_1m_tokens'` — reserved ahead of time for the future search/
tool-call fee (CC-9, out of scope here: no confirmed per-call price from Anthropic/OpenAI yet,
and Gemini grounding bills a shared account-wide monthly pool, not a per-run count), so that
future work is a data change, not a schema migration. Not dead code left over by mistake.

The backfill replays *all* of `ai_model_price_history`, not just today's prices, converting
per-1k to per-1M (* 1000) and keeping each row's original `effective_from`/`changed_by_user_id`
— so time-aware price resolution (CC-2) has real historical data from day one instead of every
component appearing to have been priced "as of now". A safety-net pass then covers any model
whose current `ai_models.cost_per_1k_*_usd` doesn't match its latest history row (or has no
history row at all) — `app/routers/ai_models.py`'s `_record_price_history` keeps both in sync
today, so this should be a no-op, but this migration runs against two independently-evolved
databases (local PC + VPS, see docs/TASKS_COST_COMPONENTS.md "Kontext a zjištění"), which makes
that guarantee worth checking rather than assuming.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-16

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

COMPONENT_TYPES = ("input", "output", "cache_read", "cache_write", "cache_write_5m", "cache_write_1h")
UNITS = ("per_1m_tokens", "per_call")


def upgrade() -> None:
    op.create_table(
        "ai_model_price_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ai_model_id", sa.Integer(), sa.ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_type", sa.String(30), nullable=False),
        sa.Column("price_per_unit_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False, server_default="per_1m_tokens"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "component_type IN ('" + "', '".join(COMPONENT_TYPES) + "')",
            name="ck_ai_model_price_components_component_type",
        ),
        sa.CheckConstraint(
            "unit IN ('" + "', '".join(UNITS) + "')",
            name="ck_ai_model_price_components_unit",
        ),
    )
    op.create_index(
        "idx_price_components_lookup",
        "ai_model_price_components",
        ["ai_model_id", "component_type", sa.text("effective_from DESC")],
    )

    # Replay the full price_history log (per-1k -> per-1M) as input/output components.
    op.execute(
        """
        INSERT INTO ai_model_price_components
            (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
        SELECT ai_model_id, 'input', cost_per_1k_input_usd * 1000, 'per_1m_tokens', effective_from, changed_by_user_id
        FROM ai_model_price_history
        WHERE cost_per_1k_input_usd IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_price_components
            (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
        SELECT ai_model_id, 'output', cost_per_1k_output_usd * 1000, 'per_1m_tokens', effective_from, changed_by_user_id
        FROM ai_model_price_history
        WHERE cost_per_1k_output_usd IS NOT NULL
        """
    )

    # Safety net: cover any model whose current price isn't reflected by its latest history row.
    op.execute(
        """
        WITH latest_history AS (
            SELECT DISTINCT ON (ai_model_id)
                ai_model_id, cost_per_1k_input_usd, cost_per_1k_output_usd
            FROM ai_model_price_history
            ORDER BY ai_model_id, effective_from DESC
        )
        INSERT INTO ai_model_price_components
            (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
        SELECT m.id, 'input', m.cost_per_1k_input_usd * 1000, 'per_1m_tokens', m.updated_at, NULL
        FROM ai_models m
        LEFT JOIN latest_history lh ON lh.ai_model_id = m.id
        WHERE m.cost_per_1k_input_usd IS NOT NULL
          AND (lh.ai_model_id IS NULL OR lh.cost_per_1k_input_usd IS DISTINCT FROM m.cost_per_1k_input_usd)
        """
    )
    op.execute(
        """
        WITH latest_history AS (
            SELECT DISTINCT ON (ai_model_id)
                ai_model_id, cost_per_1k_input_usd, cost_per_1k_output_usd
            FROM ai_model_price_history
            ORDER BY ai_model_id, effective_from DESC
        )
        INSERT INTO ai_model_price_components
            (ai_model_id, component_type, price_per_unit_usd, unit, effective_from, changed_by_user_id)
        SELECT m.id, 'output', m.cost_per_1k_output_usd * 1000, 'per_1m_tokens', m.updated_at, NULL
        FROM ai_models m
        LEFT JOIN latest_history lh ON lh.ai_model_id = m.id
        WHERE m.cost_per_1k_output_usd IS NOT NULL
          AND (lh.ai_model_id IS NULL OR lh.cost_per_1k_output_usd IS DISTINCT FROM m.cost_per_1k_output_usd)
        """
    )


def downgrade() -> None:
    op.drop_index("idx_price_components_lookup", table_name="ai_model_price_components")
    op.drop_table("ai_model_price_components")
