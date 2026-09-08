"""Add prompts.root_prompt_id and is_current_version, enabling real prompt editing.

Editing a prompt (app/routers/prompts.py) creates a new row at version+1
rather than mutating the existing one (NFR-6) — root_prompt_id links every
version of the same logical prompt together (NULL on the original,
pointing at the original's id on every later version), and
is_current_version marks which one is "the" current version for listing
purposes. Existing rows all default to is_current_version=true with no
root_prompt_id, which is already correct: every prompt created so far is
its own version 1.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompts", sa.Column("root_prompt_id", sa.Integer(), nullable=True))
    op.add_column(
        "prompts",
        sa.Column("is_current_version", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_foreign_key("fk_prompts_root_prompt_id_prompts", "prompts", "prompts", ["root_prompt_id"], ["id"])
    op.create_index("idx_prompts_root_prompt_id", "prompts", ["root_prompt_id"])


def downgrade() -> None:
    op.drop_index("idx_prompts_root_prompt_id", table_name="prompts")
    op.drop_constraint("fk_prompts_root_prompt_id_prompts", "prompts", type_="foreignkey")
    op.drop_column("prompts", "is_current_version")
    op.drop_column("prompts", "root_prompt_id")
