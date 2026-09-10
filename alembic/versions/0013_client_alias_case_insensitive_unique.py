"""Enforce case-insensitive uniqueness on client_aliases at the DB level.

The app-level duplicate check in app/routers/clients.py was already
case-insensitive, but the UniqueConstraint from migration 0011 was
exact-match only — a concurrent or sequential case-variant submission
("Acme" vs "acme") could still create two rows for what the matching
engine (re.IGNORECASE) treats as one alias. Replaces the plain unique
constraint with a functional unique index on (client_id, lower(alias)) so
the DB itself is the source of truth, not just the app-level pre-check.
Code review finding, docs/TASKS_PHASE3.md.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-10

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_client_aliases_client_id_alias", "client_aliases", type_="unique")
    op.create_index(
        "uq_client_aliases_client_id_lower_alias",
        "client_aliases",
        ["client_id", sa.text("lower(alias)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_client_aliases_client_id_lower_alias", table_name="client_aliases")
    op.create_unique_constraint("uq_client_aliases_client_id_alias", "client_aliases", ["client_id", "alias"])
