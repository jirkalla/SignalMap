"""Add run_queue.retry_of_id — the link from a retried queue item back to the one it retries.

docs/TASKS_SCHEDULER.md T7: retrying a failed/skipped/cancelled queue item always inserts a NEW
row rather than resetting the old one back to 'queued' (history is never rewritten, same NFR-6
discipline as evidence). Without a column to record which row a retry is a retry OF, that link
would have nowhere to live — `run_queue` has no generic JSONB payload column the way
`notification_outbox` does. Self-referencing, nullable (every non-retried row leaves it NULL), no
`ON DELETE` behavior — a retried row's original stays queryable by id even if something else were
ever to change about it, matching every other historical link in this table.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-21

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_queue",
        sa.Column("retry_of_id", sa.Integer(), sa.ForeignKey("run_queue.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_queue", "retry_of_id")
