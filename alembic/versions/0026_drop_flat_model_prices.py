"""Drop the flat per-model price columns and the old price-history table (docs/
TASKS_COST_COMPONENTS.md CC-8, design decisions 5 and 12) — the "clean cut" design decision 5
called for, deferred to this last task (design decision 12) so the app kept starting cleanly at
every step between CC-1 and CC-7.

`ai_model_price_components` (migration 0025) has been the only thing `estimate_run_cost`/
`run_cost_sql_expr` read since CC-4; nothing in app/ or tests/ reads `ai_models.cost_per_1k_*_usd`
or `ai_model_price_history` any more — verified with `grep -rn
"cost_per_1k|price_history|AIModelPriceHistory" app tests` before writing this migration. Every
remaining hit after this migration lands is either a docstring/comment referencing the retired
columns for historical context, or an i18n/UI key that happens to share the "price_history" name
with the *new* component-based history section (app/templates/ai_models/form.html's "Price
history" table, which reads `AIModelPriceComponent` via `_price_history_rows`, not this table) —
never an actual read of what this migration drops.

`downgrade()` recreates the column/table shapes EMPTY, not populated — going from per-1M
(NUMERIC(12,6)) back to per-1k (NUMERIC(10,5)) is lossy exactly for the values this migration
exists to leave behind (design decision 11: e.g. $0.025/1M has no exact per-1k representation in
NUMERIC(10,5) without rounding), so silently backfilling on downgrade would reintroduce the same
rounding error this whole CC-1..CC-8 effort was built to eliminate. A downgrade here is a schema
rollback, not a data-preserving migration — recovering actual historical prices after a downgrade
means reading them back out of `ai_model_price_components` by hand.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-16

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_price_history_model", table_name="ai_model_price_history")
    op.drop_table("ai_model_price_history")
    op.drop_column("ai_models", "cost_per_1k_input_usd")
    op.drop_column("ai_models", "cost_per_1k_output_usd")


def downgrade() -> None:
    op.add_column("ai_models", sa.Column("cost_per_1k_input_usd", sa.Numeric(10, 5), nullable=True))
    op.add_column("ai_models", sa.Column("cost_per_1k_output_usd", sa.Numeric(10, 5), nullable=True))
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
