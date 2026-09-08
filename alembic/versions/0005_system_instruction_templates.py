"""Add system_instruction_templates: per-provider editable locale-hint template.

Seeds the current Gemini default so the /settings page shows the real text
already in use, rather than an empty field backed by a hidden code
fallback. A provider with no row falls back to the built-in default in
app.routers.runs; a row with an empty template means that provider
explicitly gets no system_instruction at all.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_DEFAULT_TEMPLATE = (
    "The person asking this question is located in {label} and writing in "
    "{language}. Answer in {language}, using regional context and examples "
    "relevant there where applicable."
)


def upgrade() -> None:
    op.create_table(
        "system_instruction_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("providers.id"), nullable=False, unique=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO system_instruction_templates (provider_id, template)
            SELECT id, :template FROM providers WHERE code = 'google_gemini'
            """
        ).bindparams(template=_DEFAULT_TEMPLATE)
    )


def downgrade() -> None:
    op.drop_table("system_instruction_templates")
