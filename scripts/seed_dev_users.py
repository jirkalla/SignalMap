"""Seed a test editor and a test viewer account — local development only.

For quickly exercising role-based behavior (docs/TASKS_PHASE6.md P6-T6) without hand-creating
throwaway accounts through the /users UI every time you reset your local database. Unlike
scripts/create_admin.py, these credentials are deliberately hardcoded, not read from .env:
they exist only on your own local, disposable Postgres container — nothing external can ever
reach them, so there is nothing a secret would protect here. This matches how Rails/Laravel
seed scripts handle fixture accounts (fixed fake credentials in the seed script itself), not
how a real environment's admin bootstrap works.

Refuses to run when ENVIRONMENT=production (app/config.py) — these are throwaway fixtures, never
meant to exist outside a developer's own machine.

Idempotent: safe to run every time you reset your local database.

    docker compose exec app python -m scripts.seed_dev_users
"""

import sys

from fastapi_users.password import PasswordHelper
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import User

_PASSWORD = "DevPass123!"
_TEST_USERS = [
    {"email": "editor@dev.local", "name": "Test Editor", "role": "editor"},
    {"email": "viewer@dev.local", "name": "Test Viewer", "role": "viewer"},
]


def main() -> None:
    if get_settings().environment == "production":
        print("Refusing to run: ENVIRONMENT=production. These are local-dev-only fixture accounts.")
        sys.exit(1)

    db = SessionLocal()
    try:
        for spec in _TEST_USERS:
            existing = db.scalar(select(User).where(func.lower(User.email) == spec["email"]))
            if existing is not None:
                print(f"{spec['email']} already exists (id={existing.id}) — skipping.")
                continue
            user = User(
                email=spec["email"],
                hashed_password=PasswordHelper().hash(_PASSWORD),
                name=spec["name"],
                role=spec["role"],
                must_change_password=False,  # convenience fixtures, not real onboarding
                is_active=True,
                is_verified=False,
                is_superuser=False,
            )
            db.add(user)
            db.commit()
            print(f"Created {spec['role']} user {spec['email']!r} (id={user.id}), password: {_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
