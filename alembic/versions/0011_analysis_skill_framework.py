"""Add analysis_skills/analysis_results/client_aliases, and clients.domain.

First entry in the analysis layer (docs/TASKS_PHASE3.md) — deliberately
excluded from schema_phase1.sql by design (see its header comment). The
mention_visibility skill seeded here is rule_based (deterministic
regex/string match, no LLM call); execution_type also allows llm_prompt for
a future skill (e.g. sentiment) without another schema rework — see
docs/TASKS_PHASE3.md design decision 1.

clients.domain and client_aliases support the skill's matching: domain is
nullable (a client without one simply skips citation-matching, not an
error), aliases are a separate lookup table (like markets) rather than a
free-text/array column, so they get real validation and a simple CRUD UI.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

OUTPUT_SCHEMA_JSON = """{
    "text_mentioned": "boolean",
    "mention_count": "integer",
    "first_mention_position": "integer|null",
    "matched_terms": "array of matched name/alias strings",
    "cited": "boolean",
    "cited_domains": "array of matching citation source_domain values"
}"""


def upgrade() -> None:
    op.add_column("clients", sa.Column("domain", sa.String(200), nullable=True))

    op.create_table(
        "client_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("client_id", "alias", name="uq_client_aliases_client_id_alias"),
    )

    op.create_table(
        "analysis_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("execution_type", sa.String(20), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_response_id",
            sa.Integer(),
            sa.ForeignKey("raw_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_skill_id", sa.Integer(), sa.ForeignKey("analysis_skills.id"), nullable=False),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("output", JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_analysis_results_raw_response", "analysis_results", ["raw_response_id"])

    op.execute(
        f"""
        INSERT INTO analysis_skills (key, name, version, execution_type, output_schema, is_active)
        VALUES (
            'mention_visibility',
            'Mention & Visibility Detection',
            1,
            'rule_based',
            '{OUTPUT_SCHEMA_JSON}'::jsonb,
            TRUE
        )
        """
    )


def downgrade() -> None:
    op.drop_index("idx_analysis_results_raw_response", table_name="analysis_results")
    op.drop_table("analysis_results")
    op.drop_table("analysis_skills")
    op.drop_table("client_aliases")
    op.drop_column("clients", "domain")
