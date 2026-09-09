"""Add created_at/updated_at audit columns to markets and prompt_sets.

Only these two tables — both are actually editable via the app (markets
already have full CRUD; prompt_sets gained an edit endpoint in 0007's
companion work). providers/ai_models get no such columns since they have
no edit path in this branch (admin UI deferred to phase 2). See
docs/TASKS_HARDENING.md design decision 7 for the full reasoning,
including why created_by/updated_by is deliberately not added (no auth
yet in phase 1).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "markets",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "prompt_sets",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_column("prompt_sets", "updated_at")
    op.drop_column("markets", "updated_at")
    op.drop_column("markets", "created_at")
