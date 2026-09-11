"""Add indexes supporting the phase 4 dashboard's aggregation queries.

No schema shape change — index-only migration. `citations.source_domain`
backs the league table's GROUP BY, `prompts.prompt_set_id` backs the
client-scoping JOIN every dashboard query does (runs -> prompts ->
prompt_sets -> client_id), `runs.started_at` backs the date-range filter.
See docs/TASKS_PHASE4.md P4-T1.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_citations_source_domain", "citations", ["source_domain"])
    op.create_index("idx_prompts_prompt_set", "prompts", ["prompt_set_id"])
    op.create_index("idx_runs_started_at", "runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_runs_started_at", table_name="runs")
    op.drop_index("idx_prompts_prompt_set", table_name="prompts")
    op.drop_index("idx_citations_source_domain", table_name="citations")
