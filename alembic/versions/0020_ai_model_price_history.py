"""Add ai_model_price_history: append-only log of every AIModel price change.

`ai_models.cost_per_1k_input_usd`/`cost_per_1k_output_usd` stay the "current" price — nothing
that reads them today changes. This table exists purely so a price change never overwrites the
previous price without a trace (docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T1, design decision
1). Only `effective_from` is stored, not `effective_to` — the end of a row's validity is always
the next row's `effective_from` for the same model (or "now" for the latest row), computed at
read time (CPH-T2). Storing both would mean two values that must stay in sync (a row's
`effective_to` matching the next row's `effective_from` exactly) — a redundancy this project
avoids, same reasoning as never letting two independent facts drift out of sync elsewhere in
the schema.

Every existing `ai_models` row gets one backfilled history row at its own `created_at` (not
`now()`), so a model's price history starts at its real origin, not at the moment this
migration ran.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ai_model_id", sa.Integer(), sa.ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_per_1k_input_usd", sa.Numeric(10, 5), nullable=True),
        sa.Column("cost_per_1k_output_usd", sa.Numeric(10, 5), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index(
        "idx_price_history_model",
        "ai_model_price_history",
        ["ai_model_id", "effective_from"],
    )

    op.execute(
        """
        INSERT INTO ai_model_price_history (ai_model_id, cost_per_1k_input_usd, cost_per_1k_output_usd, effective_from)
        SELECT id, cost_per_1k_input_usd, cost_per_1k_output_usd, created_at
        FROM ai_models
        """
    )


def downgrade() -> None:
    op.drop_index("idx_price_history_model", table_name="ai_model_price_history")
    op.drop_table("ai_model_price_history")
