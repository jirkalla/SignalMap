"""Add the verification_labels table.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T14, design decision 27 — the
gate before `auto_verify_citations` can be trusted more broadly: a human's own verdict on a
citation, either blind (no LLM verdict shown) or reviewing one the LLM already gave. See
app/models/verification.py's `VerificationLabel` docstring for the full column-by-column
rationale, and its `HUMAN_VERDICTS`/`MODES` tuples for where the two CHECK constraints below come
from — hardcoded independently here rather than imported, same reason migration 0037
(citation_verifications) hardcodes its own copy of VERDICTS/UNVERIFIABLE_REASONS: a migration
must stay runnable against whatever the model looked like when it was written, not whatever it
looks like today.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-29

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

HUMAN_VERDICTS = ("supported", "partially_supported", "not_supported", "contradicted")
MODES = ("blind", "review")


def upgrade() -> None:
    op.create_table(
        "verification_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("citation_id", sa.Integer(), sa.ForeignKey("citations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("verdict", sa.String(25), nullable=True),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("agrees_with_verification_id", sa.Integer(), sa.ForeignKey("citation_verifications.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("mode IN ('" + "', '".join(MODES) + "')", name="ck_verification_labels_mode"),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('" + "', '".join(HUMAN_VERDICTS) + "')",
            name="ck_verification_labels_verdict",
        ),
    )
    op.create_index("idx_verification_labels_citation", "verification_labels", ["citation_id"])
    op.create_index("idx_verification_labels_user", "verification_labels", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_verification_labels_user", table_name="verification_labels")
    op.drop_index("idx_verification_labels_citation", table_name="verification_labels")
    op.drop_table("verification_labels")
