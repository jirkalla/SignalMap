"""Add runs.request_payload, so the exact request sent to a provider is inspectable.

Populated before the adapter is called (see app/routers/runs.py), so it's
present for both successful and failed runs — a failed run's request is
now debuggable too, not just its error message.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("request_payload", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "request_payload")
