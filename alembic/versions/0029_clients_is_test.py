"""Add clients.is_test, the flag that keeps a test client's runs out of the /ops aggregates.

Runs made while validating against real providers with real keys (which can only happen on
production — a local stack has no real grounding results to check) counted into /ops run volume,
success rate and cost estimates exactly like a paying client's, with no way to separate them. That
matters because those numbers are what the cost of operating the service is read from, and through
the agreed markup also what gets invoiced (docs/TASKS_PRE_SCHEDULER.md PRE-1).

Deliberately a boolean flag rather than a `client_type` enum (design decision 13): there is exactly
one question today — does this client count into summaries or not — and an enum would force
inventing the difference between 'demo' and 'internal' before anything needs it.

The flag is evaluated per query and never stored on a run, so it applies to a client's WHOLE
history, not just runs made after it was set (design decision 15). Nothing in `runs`,
`raw_responses` or `citations` is touched by this migration or by the flag — NFR-6 is not in play.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-19

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("clients", "is_test")
