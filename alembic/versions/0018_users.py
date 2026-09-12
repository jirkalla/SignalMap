"""Add users table and Run.triggered_by_user_id.

Auth backend's schema half (docs/TASKS_PHASE6.md P6-T1, docs/ROADMAP.md §1). email/
hashed_password/is_active/is_superuser/is_verified are fastapi-users' own SQLAlchemyBaseUserTable
columns (app/models/user.py); name/role/must_change_password/created_at/updated_at are this
project's additions. role uses a CHECK constraint, not a DB enum type — same idiom as
domain_type on domain_classifications (migration 0016) and execution_type on analysis_skills.

triggered_by_user_id is nullable: a future scheduler-triggered run (docs/ROADMAP.md §5) has no
human behind it, so trigger_type='scheduled' + triggered_by_user_id=None together are
unambiguous — no separate "system" user account needed.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

ROLES = ("admin", "editor", "viewer")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('" + "', '".join(ROLES) + "')",
            name="ck_users_role",
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.add_column("runs", sa.Column("triggered_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_runs_triggered_by_user_id",
        "runs",
        "users",
        ["triggered_by_user_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_runs_triggered_by_user_id", "runs", type_="foreignkey")
    op.drop_column("runs", "triggered_by_user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
