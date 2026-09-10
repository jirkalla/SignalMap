"""Document the mention_visibility skill's new match_spans output field.

Data-only migration — no table/column change. app/analysis/mention_visibility.py
now also returns match_spans (list of [start, end) pairs into rendered_text,
used to highlight matches in runs/detail.html — docs/TASKS_PHASE3.md design
decision 12). analysis_skills.output_schema is a documentation-only JSONB
column (not enforced), but keeping it in sync with the actual output shape
matters so it doesn't quietly drift out of date.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-10

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NEW_FIELD_DESCRIPTION = "array of [start, end) pairs into rendered_text, used to highlight matches"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE analysis_skills
        SET output_schema = output_schema || jsonb_build_object('match_spans', '{NEW_FIELD_DESCRIPTION}')
        WHERE key = 'mention_visibility'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE analysis_skills
        SET output_schema = output_schema - 'match_spans'
        WHERE key = 'mention_visibility'
        """
    )
