"""Add 'http_other' to citation_verifications.reason's allowed values.

Root-cause fix for the pattern migrations 0038 (http_410) and 0041 (http_429) each patched
reactively (docs/TASKS_CITATION_VERIFICATION.md code-review, 2026-09-30): `source_capture.py`
used to fall back to an unbounded `f"http_{status}"` string for any 4xx status it didn't
special-case, so the *next* unhandled code (401, 402, 405, 406, 451, ...) would hit this exact
CHECK constraint again — the underlying generator, not any one status code, was the bug.
`source_capture.py` now maps every 4xx besides 404/410/429 to this one generic 'http_other'
bucket instead, closing the gap for every future status code at once.

Widens the existing CHECK rather than replacing it (design decision 28's list otherwise
unchanged) — hardcoded independently here, same reason migrations 0037/0038/0041 hardcode their
own copy rather than importing app.models.verification: a migration must stay runnable against
whatever the model looked like when it was written, not whatever it looks like today.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-30

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

OLD_REASONS = (
    "http_403",
    "http_404",
    "http_410",
    "http_429",
    "http_5xx",
    "timeout",
    "bot_challenge",
    "robots",
    "too_large",
    "pdf_no_text",
    "no_checkable_text",
    "archive_unavailable",
)

NEW_REASONS = OLD_REASONS + ("http_other",)


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
