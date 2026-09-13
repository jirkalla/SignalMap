"""Add personas: data-driven persona a system_instruction template can frame the asker as.

Replaces the hardcoded "The person asking..." wording (`app/routers/settings.py`'s
`DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE`) with a `{persona}` placeholder backed by this table
(docs/TASKS_CHATGPT_PERSONA_PRICING.md CPH-T3 — that wiring is CPH-T5, not here). `label` is a
free-text field (design decision 3) — it's substituted directly into the English
system_instruction template sent to the provider, not developer-authored UI copy, so it doesn't
go through the DE/EN `t()` i18n layer like Market.label doesn't either.

Exactly one row may have `is_default = TRUE`, enforced by a partial unique index (design
decision 4) — the app-level default-swap always clears the old default before setting a new one
in the same transaction, so this index should never actually reject a legitimate write.

Seeds the single 'person' row as the default so today's runs keep producing the exact same
system_instruction text as before this migration — nothing changes until an admin adds more
personas via CPH-T4's admin UI.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False, unique=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_personas_one_default",
        "personas",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.execute("INSERT INTO personas (label, is_default) VALUES ('person', TRUE)")


def downgrade() -> None:
    op.drop_index("idx_personas_one_default", table_name="personas")
    op.drop_table("personas")
