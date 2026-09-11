"""Add domain_classifications: manual editorial classification of cited domains.

Keyed by normalized domain (not by citation row) — one classification serves every citation
of that domain across every client, since "iea.org is Institutional" doesn't vary per client.
Deliberately excludes a "competitor" category — that's already derivable from
tracked_entities.domain (docs/TASKS_PHASE5.md P5-T3), a second hand-maintained copy of the
same information would be a duplicate source of truth. See docs/TASKS_PHASE5.md P5-T2 and the
"Návrh A" schema flag for the full design rationale, confirmed by the user before this
migration was written.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

DOMAIN_TYPES = ("institutional", "editorial", "corporate", "reference", "ugc", "other")


def upgrade() -> None:
    op.create_table(
        "domain_classifications",
        sa.Column("domain", sa.String(255), primary_key=True),
        sa.Column("domain_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "domain_type IN ('" + "', '".join(DOMAIN_TYPES) + "')",
            name="ck_domain_classifications_domain_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("domain_classifications")
