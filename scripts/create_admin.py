"""Bootstrap the first admin account.

The /users admin UI (app/routers/users.py) is admin-only — it needs an existing admin to
create anyone else, so a fresh install has no way to reach it. This script is the one place
account creation happens outside that UI, for exactly that bootstrap moment. Safe to run again
later too: refuses to create a second account for an email that already exists, same
case-insensitive duplicate check the UI uses.

Uses the same synchronous PasswordHelper hashing as app/routers/users.py, not fastapi-users'
async UserManager.create() — this script has no running event loop, and password hashing
itself is synchronous CPU work regardless.

Run from inside the app container, as a module (not a bare file path) so `app.*` imports
resolve against the container's /code working directory:

    docker compose exec app python -m scripts.create_admin --email admin@example.com --name "Jiri Vosta"

Password is prompted interactively, never accepted as a CLI argument — an argument would sit
in shell history and process listings.
"""

import argparse
import getpass
import sys

from fastapi_users.password import PasswordHelper
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import User
from app.models.user import ROLES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="Login email for the new account.")
    parser.add_argument("--name", required=True, help="Display name shown in the app header and user list.")
    parser.add_argument(
        "--role",
        default="admin",
        choices=ROLES,
        help="Defaults to admin — this script exists to bootstrap the first admin account.",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(func.lower(User.email) == email))
        if existing is not None:
            print(f"A user with email {email!r} already exists (id={existing.id}, role={existing.role}).")
            sys.exit(1)

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords don't match.")
            sys.exit(1)
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            sys.exit(1)

        user = User(
            email=email,
            hashed_password=PasswordHelper().hash(password),
            name=args.name.strip(),
            role=args.role,
            must_change_password=True,
            is_active=True,
            is_verified=False,
            is_superuser=False,
        )
        db.add(user)
        db.commit()
        print(f"Created {args.role} user {email!r} (id={user.id}). They must change this password on first login.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
