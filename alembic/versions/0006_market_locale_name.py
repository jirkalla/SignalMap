"""Rename markets.label to markets.locale_name; rename system_instruction

placeholders to be consistently market_-prefixed ({language} -> {market_language},
{country} -> {market_country}, {label} -> {market_locale_name}), future-proofing
against ambiguity once placeholders from other sources (provider, model) exist.

Rewrites any saved system_instruction_templates rows that still use the old
placeholder names, not just the seeded default — a clean break rather than
a dual-compatibility shim, since no real client has customized this yet.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("markets", "label", new_column_name="locale_name")
    op.execute(
        """
        UPDATE system_instruction_templates
        SET template = replace(replace(replace(template,
            '{label}', '{market_locale_name}'),
            '{language}', '{market_language}'),
            '{country}', '{market_country}')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE system_instruction_templates
        SET template = replace(replace(replace(template,
            '{market_locale_name}', '{label}'),
            '{market_language}', '{language}'),
            '{market_country}', '{country}')
        """
    )
    op.alter_column("markets", "locale_name", new_column_name="label")
