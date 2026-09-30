"""Add throttle_state — cross-process request-pacing state.

Code-review finding, 2026-09-30 (docs/TASKS_CITATION_VERIFICATION.md): `source_capture.py`'s
per-domain throttle and `archive_lookup.py`'s throttle used to be plain in-process module
variables, which production's 4 worker processes (`docker compose --scale worker=4`) could each
pace independently against — multiplying the documented per-target request rate by however many
processes happened to hit the same domain/archive.org at once. This table gives every process the
same shared clock to reserve slots against instead (`app/services/rate_limit.py`).

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-30

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "throttle_state",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("next_available_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("throttle_state")
