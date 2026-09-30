"""Add clients.vision — how the client wants AI assistants to describe it.

docs/ROADMAP.md #23, docs/TASKS_CLIENT_VISION.md. Free text (no structure, decision 2), separate
from `clients.notes`: Vision is the client's desired perception (a future input for the gap
score / executive summary), Notes are the agency's internal remarks. The length limit is enforced
in the router, not the database, so it can change without a migration.

Additive only — a nullable column with no backfill; existing clients simply have NULL. Safe to
apply before the new code is deployed (docs/DEPLOYMENT.md 1.3).

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-30

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("vision", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "vision")
