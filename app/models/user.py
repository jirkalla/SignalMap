"""User — login account for the auth backend (docs/TASKS_PHASE6.md P6-T1)."""

from datetime import datetime

from fastapi_users.db import SQLAlchemyBaseUserTable
from fastapi_users.password import PasswordHelper
from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Same idiom as DOMAIN_TYPES (app/models/domain_classification.py) — adding a role means
# extending this tuple, not changing the framework. Migration 0018 hardcodes its own copy of
# this tuple for the CHECK constraint independently (Alembic migrations don't import
# application code) — changing this tuple later needs a new migration to ALTER that constraint.
ROLES = ("admin", "editor", "viewer")


class User(SQLAlchemyBaseUserTable[int], Base):
    """A login account. `email`/`hashed_password`/`is_active`/`is_superuser`/`is_verified` come
    from fastapi-users' SQLAlchemyBaseUserTable mixin — don't redeclare them here.

    `is_superuser` is left at its library default (False) and unused — this project's
    authorization is driven entirely by `role` (see `app.auth.require_role`), not by
    fastapi-users' own superuser flag, so there is exactly one axis of "what can this account
    do" instead of two that could drift out of sync.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('" + "', '".join(ROLES) + "')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def display_label(self) -> str:
        """The short label shown in the header — self-service `display_name` if the account set
        one, else initials computed from `name` (first letter of the first and last word), so an
        unset display name never means showing the full, potentially long, `name` there.
        """
        if self.display_name:
            return self.display_name
        parts = self.name.split()
        if not parts:
            return self.name
        if len(parts) == 1:
            return parts[0][0].upper()
        return (parts[0][0] + parts[-1][0]).upper()


def build_user(
    *,
    email: str,
    password: str,
    name: str,
    role: str,
    must_change_password: bool = True,
    is_active: bool = True,
    is_verified: bool = False,
) -> User:
    """Construct a valid `User` row with a properly hashed password — the one shared place every
    account-creation path in this app builds one, instead of each re-typing the same
    `PasswordHelper().hash(...)` + field list (`app/routers/users.py`'s `create_user`,
    `scripts/create_admin.py`'s two modes, `scripts/seed_dev_users.py`, and `tests/conftest.py`'s
    test fixtures all called this a "same pattern as ..." before this existed — a code review
    caught that pattern already silently diverging between two of those call sites on `is_active`
    handling). Caller is responsible for uniqueness-checking `email` first and adding/committing
    the returned row.
    """
    return User(
        email=email,
        hashed_password=PasswordHelper().hash(password),
        name=name,
        role=role,
        must_change_password=must_change_password,
        is_active=is_active,
        is_verified=is_verified,
        is_superuser=False,
    )
