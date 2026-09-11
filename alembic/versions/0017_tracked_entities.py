"""Add tracked_entities/tracked_entity_aliases, and seed competitive_visibility skill.

Second-generation analysis skill's schema half (docs/TASKS_PHASE5.md P5-T3) — competitor
tracking per client, backing the coming competitive_visibility rule_based skill (share of
voice / position among mentioned entities in a run). The client itself never gets a row here
— the skill merges Client.name/ClientAlias (phase 3) with tracked_entities at run time, so the
client's own identity keeps exactly one source of truth, not a duplicated copy (design
decision 1).

Case-insensitive uniqueness is a functional index from the start here, unlike client_aliases,
which only got this as a later fix in migration 0013 — same rationale (the matching engine
itself is case-insensitive, so the DB should be the source of truth for that, not just an
app-level pre-check), applied up front this time instead of as a follow-up.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OUTPUT_SCHEMA_JSON = """{
    "entities": "array of {name, is_own_client, mentioned, mention_count, first_position, cited, cited_domains}",
    "share_of_voice": "float|null - share of mention_count held by the client itself among all tracked entities in this run; null when no tracked entity was mentioned at all",
    "position": "integer|null - rank of the client itself by first_position among mentioned entities; null when the client itself was not mentioned"
}"""


def upgrade() -> None:
    op.create_table(
        "tracked_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_tracked_entities_client_id_lower_name",
        "tracked_entities",
        ["client_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "tracked_entity_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tracked_entity_id",
            sa.Integer(),
            sa.ForeignKey("tracked_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_tracked_entity_aliases_entity_id_lower_alias",
        "tracked_entity_aliases",
        ["tracked_entity_id", sa.text("lower(alias)")],
        unique=True,
    )

    op.execute(
        f"""
        INSERT INTO analysis_skills (key, name, version, execution_type, output_schema, is_active)
        VALUES (
            'competitive_visibility',
            'Competitive Visibility Detection',
            1,
            'rule_based',
            '{OUTPUT_SCHEMA_JSON}'::jsonb,
            TRUE
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM analysis_skills WHERE key = 'competitive_visibility'")
    op.drop_index("uq_tracked_entity_aliases_entity_id_lower_alias", table_name="tracked_entity_aliases")
    op.drop_table("tracked_entity_aliases")
    op.drop_index("uq_tracked_entities_client_id_lower_name", table_name="tracked_entities")
    op.drop_table("tracked_entities")
