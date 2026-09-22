"""Add run_queue.transport_attempts — a separate counter from attempts for real provider tries.

Found in code review, 2026-09-22: `run_queue.attempts` is incremented on every `claim_next` claim,
including one that immediately defers for an unrelated same-prompt-and-model collision (design
decision 17). `app/worker.py`'s transport-retry-exhaustion check (`_MAX_TRANSPORT_ATTEMPTS`) was
reading that same counter, so an item that collided a few times before ever reaching a provider
could have its very first genuine transport failure treated as already exhausted, skipping the
1/5/25-minute backoff it was supposed to get. `transport_attempts` is bumped only in
`process_claimed_item`'s transport-failure branch; `attempts`/collision backoff is unchanged.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-22

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_queue",
        sa.Column("transport_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("run_queue", "transport_attempts")
