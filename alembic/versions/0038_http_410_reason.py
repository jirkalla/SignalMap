"""Add 'http_410' to citation_verifications.reason's allowed values.

Schema-only migration for docs/TASKS_CITATION_VERIFICATION.md T10, design decision 19: the
archive.org fallback treats a live capture that came back 404 OR 410 ("Gone") the same way, but
`source_capture.py` (T4) has always been able to produce `error_reason="http_410"` on
`source_documents` (that column is free-form, no CHECK) — the CHECK on
`citation_verifications.reason` (migration 0037) just never had a matching value, so copying a
410 across would have hit an IntegrityError the moment T10 started actually exercising that path.

Widens the existing CHECK rather than replacing it (design decision 28's list otherwise
unchanged) — hardcoded independently here, same reason migration 0037 hardcodes its own copy
rather than importing app.models.verification: a migration must stay runnable against whatever
the model looked like when it was written, not whatever it looks like today.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-29

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

OLD_REASONS = (
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

NEW_REASONS = OLD_REASONS + ("http_410",)


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
