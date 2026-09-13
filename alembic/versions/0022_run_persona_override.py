"""Add runs.persona_id, allowing a run to override which persona frames the system_instruction.

Backfills existing runs to the default persona before making the column NOT NULL, so historical
rows stay consistent — same technique as 0002_run_market_override.py for `runs.market_id`. New
rows always set it explicitly (see app/routers/runs.py's trigger_run) rather than relying on
this default going forward.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-13

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("persona_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE runs
        SET persona_id = (SELECT id FROM personas WHERE is_default)
        """
    )
    op.alter_column("runs", "persona_id", nullable=False)
    op.create_foreign_key("fk_runs_persona_id_personas", "runs", "personas", ["persona_id"], ["id"])
    op.create_index("idx_runs_persona", "runs", ["persona_id"])


def downgrade() -> None:
    op.drop_index("idx_runs_persona", table_name="runs")
    op.drop_constraint("fk_runs_persona_id_personas", "runs", type_="foreignkey")
    op.drop_column("runs", "persona_id")
