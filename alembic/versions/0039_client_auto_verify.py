"""Add clients.auto_verify_citations.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T13, design decision 25 — a single
boolean flag, default false. Controls only whether a NEW run's OpenAI/Gemini citations get the
paid LLM paraphrase check (T12) automatically, right after their sources are captured; capture
(T4) and the free literal-quote check (T8) always run regardless of this flag, for every client.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-29

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("auto_verify_citations", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("clients", "auto_verify_citations")
