"""Add source snapshot and verification job tables.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T3 — no business logic, no data
migration (nothing is backfilled here; the CLI backfill is T6). Three new tables:

  - `source_texts`     — content-addressed extracted text of one page/PDF snapshot, keyed by its
                        own sha256 (design decision 12) — identical text stored once regardless
                        of how many URLs/fetches produced it.
  - `source_documents` — one fetch attempt of one URL (live or archive.org), always a new row,
                        never updated (NFR-6, design decision 2).
  - `verification_jobs` — the capture/judge work queue, the same `SKIP LOCKED` shape as
                        `run_queue` (migration 0030) — claimed in app/services/verification_queue.py
                        (T5), always after `run_queue` is drained (design decision 3).

See app/models/verification.py for the full column-by-column rationale.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-28

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_texts",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("archive_timestamp", sa.String(14), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("error_reason", sa.String(40), nullable=True),
        sa.Column("challenge_vendor", sa.String(40), nullable=True),
        sa.Column("text_sha256", sa.String(64), sa.ForeignKey("source_texts.sha256"), nullable=True),
        sa.Column("page_starts", postgresql.JSONB(), nullable=True),
        sa.Column("locations", postgresql.JSONB(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("verifier_version", sa.String(20), nullable=False),
        sa.CheckConstraint("method IN ('live', 'archive')", name="ck_source_documents_method"),
    )
    op.create_index(
        "idx_source_documents_url_fetched",
        "source_documents",
        ["requested_url", sa.text("fetched_at DESC")],
    )

    op.create_table(
        "verification_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_response_id",
            sa.Integer(),
            sa.ForeignKey("raw_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("leased_by", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('capture', 'judge')", name="ck_verification_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'done', 'error', 'deferred')",
            name="ck_verification_jobs_status",
        ),
    )
    op.create_index(
        "idx_verification_jobs_claim_order",
        "verification_jobs",
        [sa.text("priority DESC"), "scheduled_for"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("idx_verification_jobs_claim_order", table_name="verification_jobs")
    op.drop_table("verification_jobs")

    op.drop_index("idx_source_documents_url_fetched", table_name="source_documents")
    op.drop_table("source_documents")

    op.drop_table("source_texts")
