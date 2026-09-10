"""Add search_queries: search queries a provider issued while grounding a raw response.

Captures data already present in raw_payload (Gemini's grounding_metadata.
web_search_queries, Anthropic's server_tool_use "web_search" blocks) that
wasn't parsed out before now. See docs/TASKS_SEARCH_QUERIES.md for the
design decisions and provider-field verification behind this table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-10

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_response_id",
            sa.Integer(),
            sa.ForeignKey("raw_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_position", sa.Integer()),
    )


def downgrade() -> None:
    op.drop_table("search_queries")
