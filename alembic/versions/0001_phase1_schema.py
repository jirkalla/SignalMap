"""Phase 1 schema: clients, markets, prompts, providers, runs, evidence tables.

Executes schema_phase1.sql verbatim (repo root) so this migration and that
reference file can never drift apart for the slice it covers. schema_phase1.sql
is a historical snapshot of the phase-1 vertical slice only (AI_INSTRUCTIONS.md
v1.2, design decision 38 in docs/TASKS_SCHEDULER.md) — every table added since
across later phases lives only in its own later migration, never backfilled
into that file. This migration's own history (`alembic/versions/`) is the
authoritative description of the current schema, not the SQL file it executes.
Seed data (markets, the Google provider, the two Gemini models) ships in
this same migration rather than a separate script, so `alembic upgrade
head` remains the only setup step after `docker compose up` — see the
decision note in docs/PROMPTS.md Task 1.

Revision ID: 0001
Revises:
Create Date: 2026-09-08

"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL_PATH = Path(__file__).resolve().parents[2] / "schema_phase1.sql"


def upgrade() -> None:
    op.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS citations CASCADE;
        DROP TABLE IF EXISTS raw_responses CASCADE;
        DROP TABLE IF EXISTS runs CASCADE;
        DROP TABLE IF EXISTS ai_models CASCADE;
        DROP TABLE IF EXISTS providers CASCADE;
        DROP TABLE IF EXISTS prompts CASCADE;
        DROP TABLE IF EXISTS prompt_sets CASCADE;
        DROP TABLE IF EXISTS markets CASCADE;
        DROP TABLE IF EXISTS clients CASCADE;
        """
    )
