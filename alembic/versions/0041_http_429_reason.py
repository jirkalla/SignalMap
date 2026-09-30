"""Add 'http_429' to citation_verifications.reason's allowed values.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T15's real-data run against Knauf:
`source_capture.py` (T4) has always been able to produce `error_reason="http_429"` on
`source_documents` (that column is free-form, no CHECK), but the CHECK on
`citation_verifications.reason` (migration 0037, widened by 0038 for `http_410`) never had a
matching value. `claim_judge.judge_citations` copies `document.error_reason` straight across
(`app/services/claim_judge.py`) the moment it finds a captured document that failed — a rate
limited source hit this the first time a real retroactive verify run reached one, raising an
IntegrityError on the job's single end-of-loop commit and rolling back every judgement in that
job, including already-paid LLM calls for the response's other citations (found and halted
2026-09-29 — the stuck jobs were manually marked 'error', not retried further, to stop repeat
paid LLM calls against the same unrecoverable commit).

Widens the existing CHECK rather than replacing it (design decision 28's list otherwise
unchanged) — hardcoded independently here, same reason migrations 0037/0038 hardcode their own
copy rather than importing app.models.verification: a migration must stay runnable against
whatever the model looked like when it was written, not whatever it looks like today.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-29

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

OLD_REASONS = (
    "http_403",
    "http_404",
    "http_410",
    "http_5xx",
    "timeout",
    "bot_challenge",
    "robots",
    "too_large",
    "pdf_no_text",
    "no_checkable_text",
    "archive_unavailable",
)

NEW_REASONS = OLD_REASONS + ("http_429",)


def upgrade() -> None:
    op.drop_constraint("ck_citation_verifications_reason", "citation_verifications", type_="check")
    op.create_check_constraint(
        "ck_citation_verifications_reason",
        "citation_verifications",
        "reason IS NULL OR reason IN ('" + "', '".join(NEW_REASONS) + "')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_citation_verifications_reason", "citation_verifications", type_="check")
    op.create_check_constraint(
        "ck_citation_verifications_reason",
        "citation_verifications",
        "reason IS NULL OR reason IN ('" + "', '".join(OLD_REASONS) + "')",
    )
