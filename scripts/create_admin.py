"""Bootstrap the first admin account.

The /users admin UI (app/routers/users.py) is admin-only — it needs an existing admin to
create anyone else, so a fresh install has no way to reach it. This script is the one place
account creation happens outside that UI, for exactly that bootstrap moment.

Uses the same synchronous PasswordHelper hashing as app/routers/users.py, not fastapi-users'
async UserManager.create() — this script has no running event loop, and password hashing
itself is synchronous CPU work regardless.

Run from inside the app container, as a module (not a bare file path) so `app.*` imports
resolve against the container's /code working directory:

    docker compose exec app python -m scripts.create_admin --email admin@example.com --name "Jiri Vosta"

Password is prompted interactively, never accepted as a CLI argument — an argument would sit
in shell history and process listings. Fails (exit 1) if the email is already taken — this path
is a deliberate one-time action, so silently succeeding on a typo'd duplicate would hide a
mistake rather than surface it.

Non-interactive alternative for local dev, reading DEV_ADMIN_EMAIL/DEV_ADMIN_NAME/
DEV_ADMIN_PASSWORD from .env instead of prompting (refuses to run when ENVIRONMENT=production,
same guard as scripts/seed_dev_users.py — this path reads a fixed, known password straight from
.env, which is fine for a throwaway local database but must never run against a real one):

    docker compose exec app python -m scripts.create_admin --from-env

Unlike the interactive path, --from-env is idempotent (exit 0, not 1, if the account already
exists) — it's meant to be safely re-run every time you reset your local database, not a
one-time action.

Locked yourself out (forgot/changed an admin's password)? The /users UI can't help — it needs
an existing logged-in admin, which is exactly what's missing. Reset it from here instead,
same interactive getpass prompt as account creation:

    docker compose exec app python -m scripts.create_admin --reset-password admin@example.com
"""

import argparse
import getpass
import sys

from fastapi_users.password import PasswordHelper
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import User
from app.models.user import ROLES, build_user


def _create(db, *, email: str, name: str, password: str, role: str) -> User:
    user = build_user(email=email, password=password, name=name, role=role)
    db.add(user)
    db.commit()
    return user


def _run_from_env(db) -> None:
    settings = get_settings()
    if settings.environment == "production":
        print(
            "Refusing to run: ENVIRONMENT=production. --from-env reads a fixed password from "
            "DEV_ADMIN_PASSWORD — use the interactive mode (no --from-env) in production."
        )
        sys.exit(1)

    if not settings.dev_admin_email or not settings.dev_admin_password or not settings.dev_admin_name:
        print("DEV_ADMIN_EMAIL, DEV_ADMIN_NAME, and DEV_ADMIN_PASSWORD must all be set in .env for --from-env.")
        sys.exit(1)

    email = settings.dev_admin_email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        print(f"Admin user {email!r} already exists (id={existing.id}) — nothing to do.")
        sys.exit(0)  # idempotent: safe to call this on every fresh local DB, not just once

    if len(settings.dev_admin_password) < 8:
        print("DEV_ADMIN_PASSWORD must be at least 8 characters.")
        sys.exit(1)

    user = _create(db, email=email, name=settings.dev_admin_name.strip(), password=settings.dev_admin_password, role="admin")
    print(f"Created admin user {email!r} (id={user.id}) from .env. They must change this password on first login.")


def _run_interactive(email: str, name: str, role: str, db) -> None:
    email = email.strip().lower()
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

    user = _create(db, email=email, name=name.strip(), password=password, role=role)
    print(f"Created {role} user {email!r} (id={user.id}). They must change this password on first login.")


def _run_reset_password(email: str, db) -> None:
    email = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        print(f"No user with email {email!r}.")
        sys.exit(1)

    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if password != confirm:
        print("Passwords don't match.")
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    user.hashed_password = PasswordHelper().hash(password)
    user.must_change_password = True
    user.is_active = True
    db.commit()
    print(f"Password reset for {email!r} (id={user.id}). They must change it again on next login.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", help="Login email for the new account.")
    parser.add_argument("--name", help="Display name shown in the app header and user list.")
    parser.add_argument(
        "--role",
        default="admin",
        choices=ROLES,
        help="Defaults to admin — this script exists to bootstrap the first admin account.",
    )
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Read DEV_ADMIN_EMAIL/DEV_ADMIN_NAME/DEV_ADMIN_PASSWORD from .env instead of "
        "prompting. Idempotent — safe to re-run on every fresh local database.",
    )
    parser.add_argument(
        "--reset-password",
        metavar="EMAIL",
        help="Reset an existing user's password (interactive getpass prompt) instead of "
        "creating an account — for when you're locked out and there's no other admin to use "
        "the /users UI with.",
    )
    args = parser.parse_args()

    if args.reset_password and (args.from_env or args.email or args.name):
        parser.error("--reset-password can't be combined with --from-env/--email/--name.")
    if args.from_env and (args.email or args.name):
        parser.error("--from-env can't be combined with --email/--name.")
    if not args.reset_password and not args.from_env and not (args.email and args.name):
        parser.error("--email and --name are required unless --from-env or --reset-password is given.")

    db = SessionLocal()
    try:
        if args.reset_password:
            _run_reset_password(args.reset_password, db)
        elif args.from_env:
            _run_from_env(db)
        else:
            _run_interactive(args.email, args.name, args.role, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
