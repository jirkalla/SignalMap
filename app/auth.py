"""Authentication backend: fastapi-users wiring, cookie session, and role-based authorization.

See docs/TASKS_PHASE6.md P6-T2 and docs/ROADMAP.md §1 for the full design rationale. Cookie +
JWT strategy, not database-backed sessions (design decision 1) — the session's state lives in
the signed cookie itself, so there's no sessions table to manage or clean up; the tradeoff is
no way to force-invalidate one specific device without a revocation list, accepted for a small
internal team. Self-registration and emailed password reset are deliberately never mounted
(design decision 2) — the only way to get an account is an admin creating one (app/routers/
users.py, added in P6-T4).
"""

from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_async_db
from app.errors import AppError
from app.models import User

_SESSION_LIFETIME_SECONDS = 60 * 60 * 24 * 14  # 14 days


async def get_user_db(session: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    """fastapi-users' storage adapter — needs the async session (app/database.py), not the
    sync one the rest of the app uses.
    """
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    """Minimal user manager — deliberately no `on_after_*` hooks (no register/verify/forgot-
    password email flow exists to hook into; those routers are never mounted, see module
    docstring). The token-secret attributes are still required by `BaseUserManager` itself even
    though the routes that would use them don't exist — reusing `secret_key` costs nothing
    since those tokens are never issued.
    """

    reset_password_token_secret = get_settings().secret_key
    verification_token_secret = get_settings().secret_key


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


cookie_transport = CookieTransport(
    cookie_name="signalmap_session",
    cookie_max_age=_SESSION_LIFETIME_SECONDS,
    cookie_secure=get_settings().cookie_secure,
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=get_settings().secret_key, lifetime_seconds=_SESSION_LIFETIME_SECONDS)


auth_backend = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

# Raises HTTPException(401) (not AppError) when there's no valid session — verified against
# fastapi_users.authentication.authenticator source, not assumed. app/main.py's exception
# handler turns that into a redirect to /login for full-page navigations, and leaves it as
# JSON for the dashboard's own /api/* endpoints (design decision 6).
current_active_user = fastapi_users.current_user(active=True)


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: 403s (AppError) unless the logged-in user's role is one of
    `allowed_roles`. This is the sole source of authorization truth — a UI element hidden by
    role elsewhere in the app is convenience only, never a substitute for this check
    (docs/TASKS_PHASE6.md design decision 4).
    """

    def dependency(user: User = Depends(current_active_user)) -> User:
        if user.role not in allowed_roles:
            raise AppError("forbidden", "You don't have permission to do that.", status_code=403)
        return user

    return dependency
