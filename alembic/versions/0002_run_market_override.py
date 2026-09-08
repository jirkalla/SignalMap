"""Add runs.market_id, allowing a run to override the prompt's market.

Backfills existing runs from their prompt's current market before making
the column NOT NULL, so historical rows stay consistent. New rows always
set it explicitly (see app/routers/runs.py) rather than relying on this
default going forward.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("market_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE runs
        SET market_id = prompts.market_id
        FROM prompts
        WHERE prompts.id = runs.prompt_id
        """
    )
    op.alter_column("runs", "market_id", nullable=False)
    op.create_foreign_key("fk_runs_market_id_markets", "runs", "markets", ["market_id"], ["id"])
    op.create_index("idx_runs_market", "runs", ["market_id"])


def downgrade() -> None:
    op.drop_index("idx_runs_market", table_name="runs")
    op.drop_constraint("fk_runs_market_id_markets", "runs", type_="foreignkey")
    op.drop_column("runs", "market_id")
