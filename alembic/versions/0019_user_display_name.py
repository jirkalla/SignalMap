"""Add User.display_name.

Self-service, optional short name a logged-in user can set for themselves (any role), shown in
the header instead of the full `name` (docs/TASKS_PHASE6.md follow-up, 2026-09-12). When unset,
app/models/user.py's `User.display_label` falls back to computed initials from `name` rather than
the full name, so the header stays compact — no schema change needed for that fallback, it's
pure Python over the existing `name` column.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-12

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
