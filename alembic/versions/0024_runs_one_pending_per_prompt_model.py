"""Enforce "at most one pending run per (prompt_id, model_id)" at the database level.

Code-review finding (docs/TASKS_BULK_IMPORT_MULTI_MODEL.md branch, 2026-09-14) — `trigger_run`'s
pending-run guard (`app/routers/runs.py`) was a plain SELECT-then-INSERT with no locking, so two
near-simultaneous requests for the same prompt+model could both pass the "no pending run exists"
check before either commits, letting both fire a real, billed provider call. This closes the
race at the source: a partial unique index rejects the second concurrent INSERT outright: same
technique as `idx_personas_one_default` (migration 0021) for "at most one X" invariants. The app
already catches `IntegrityError` on the resulting commit and converts it to the same 409
`run_already_pending` response the plain SELECT check already returns for the non-racy case —
so this index is a backstop for the race window, not a replacement for the existing guard (which
still avoids the extra DB round-trip on the common, non-racy path).

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-14

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_runs_one_pending_per_prompt_model",
        "runs",
        ["prompt_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_runs_one_pending_per_prompt_model", table_name="runs")
