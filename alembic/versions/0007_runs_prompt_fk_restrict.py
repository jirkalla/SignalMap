"""Make runs.prompt_id's ON DELETE behavior explicit: RESTRICT.

Functionally identical to today's implicit Postgres default (NO ACTION —
the column had no ondelete clause at all) — this is a schema-readability
change, not a behavior change. The real gap this branch closes is at the
application layer: no delete endpoint existed for Client/PromptSet/Prompt
before this (see docs/TASKS_HARDENING.md design decision 4).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("runs_prompt_id_fkey", "runs", type_="foreignkey")
    op.create_foreign_key(
        "runs_prompt_id_fkey", "runs", "prompts", ["prompt_id"], ["id"], ondelete="RESTRICT"
    )


def downgrade() -> None:
    op.drop_constraint("runs_prompt_id_fkey", "runs", type_="foreignkey")
    op.create_foreign_key("runs_prompt_id_fkey", "runs", "prompts", ["prompt_id"], ["id"])
