"""Add an index on citations.raw_response_id — the join column every dashboard aggregation
query (summary/domains/timeseries citations metric) uses to reach a client's runs. Migration
0014 indexed citations.source_domain but missed this one; analysis_results.raw_response_id
already has idx_analysis_results_raw_response from migration 0011, citations never did.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-11

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_citations_raw_response", "citations", ["raw_response_id"])


def downgrade() -> None:
    op.drop_index("idx_citations_raw_response", table_name="citations")
