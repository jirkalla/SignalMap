"""Add the citation verification table.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T7 — no business logic, no data
migration. One new table:

  - `citation_verifications` — one verdict on one citation, append-only (NFR-6, design decision
                        2): a re-check adds a new row next to the old one, never edits it.

See app/models/verification.py's `CitationVerification` docstring for the full column-by-column
rationale, and its `VERDICTS`/`UNVERIFIABLE_REASONS` tuples for where the two CHECK constraints
below come from (design decision 28) — hardcoded independently here rather than imported, same
reason migration 0016 (domain_classifications) hardcodes its own DOMAIN_TYPES: a migration must
stay runnable against whatever the model looked like when it was written, not whatever it looks
like today.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-29

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

VERDICTS = (
    "verified_exact",
    "verified_normalized",
    "page_changed",
    "archive_only",
    "partially_found",
    "not_found",
    "llm_supported",
    "llm_partial",
    "llm_not_supported",
    "llm_contradicted",
    "unverifiable",
    "source_reachable",
)

UNVERIFIABLE_REASONS = (
    "http_403",
    "http_404",
    "http_5xx",
    "timeout",
    "bot_challenge",
    "robots",
    "too_large",
    "pdf_no_text",
    "no_checkable_text",
    "archive_unavailable",
)


def upgrade() -> None:
    op.create_table(
        "citation_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("citation_id", sa.Integer(), sa.ForeignKey("citations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("source_documents.id"), nullable=True),
        sa.Column("claim_text", sa.Text(), nullable=True),
        sa.Column("claim_method", sa.String(25), nullable=True),
        sa.Column("check_type", sa.String(15), nullable=False),
        sa.Column("verdict", sa.String(25), nullable=False),
        sa.Column("reason", sa.String(30), nullable=True),
        sa.Column("similarity", sa.Numeric(4, 3), nullable=True),
        sa.Column("matched_text", sa.Text(), nullable=True),
        sa.Column("match_start", sa.Integer(), nullable=True),
        sa.Column("match_end", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("location", postgresql.JSONB(), nullable=True),
        sa.Column("fragments", postgresql.JSONB(), nullable=True),
        sa.Column("llm_model_id", sa.Integer(), sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("llm_reason", sa.Text(), nullable=True),
        sa.Column("llm_quote", sa.Text(), nullable=True),
        sa.Column("llm_quote_found", sa.Boolean(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("verifier_version", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "check_type IN ('quote', 'llm', 'reachability')", name="ck_citation_verifications_check_type"
        ),
        sa.CheckConstraint(
            "verdict IN ('" + "', '".join(VERDICTS) + "')", name="ck_citation_verifications_verdict"
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('" + "', '".join(UNVERIFIABLE_REASONS) + "')",
            name="ck_citation_verifications_reason",
        ),
    )
    op.create_index(
        "idx_citation_verifications_citation_created",
        "citation_verifications",
        ["citation_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_citation_verifications_citation_created", table_name="citation_verifications")
    op.drop_table("citation_verifications")
