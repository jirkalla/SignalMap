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

import jwt as pyjwt
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import decode_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal, get_async_db
from app.errors import AppError
from app.i18n import get_t
from app.models import User

_SESSION_LIFETIME_SECONDS = 60 * 60 * 24 * 14  # 14 days

# Shared with app/templating.py, which decodes the same cookie synchronously (via fastapi_users'
# own decode_jwt helper) just to look up a display name for the header — not for authorization,
# which always goes through current_active_user/require_role below. Named here, not just a
# string literal repeated in two files.
SESSION_COOKIE_NAME = "signalmap_session"
JWT_AUDIENCE = ["fastapi-users:auth"]


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
    cookie_name=SESSION_COOKIE_NAME,
    cookie_max_age=_SESSION_LIFETIME_SECONDS,
    cookie_secure=get_settings().cookie_secure,
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=get_settings().secret_key,
        lifetime_seconds=_SESSION_LIFETIME_SECONDS,
        token_audience=JWT_AUDIENCE,
    )


auth_backend = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

# Raises HTTPException(401) (not AppError) when there's no valid session — verified against
# fastapi_users.authentication.authenticator source, not assumed. app/main.py's exception
# handler turns that into a redirect to /login for full-page navigations, and leaves it as
# JSON for the dashboard's own /api/* endpoints (design decision 6).
current_active_user = fastapi_users.current_user(active=True)


def current_user_from_cookie(request: Request) -> User | None:
    """Best-effort lookup of the logged-in user straight from the session cookie, for the two
    places that need "who is this" outside FastAPI's own dependency injection: `render()`
    (app/templating.py, display only — name + logout link in the header) and the
    must-change-password middleware (app/main.py, which runs before routing/dependency
    resolution even happens). Never used for authorization itself — that always goes through
    `current_active_user`/`require_role` as a real dependency. Decodes synchronously via
    fastapi-users' own `decode_jwt` (a plain function, not the async `JWTStrategy.read_token`),
    so callers that aren't already inside an async request handler don't need to bridge into one
    for something this low-stakes. Returns None on any missing/invalid/expired token.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_jwt(token, get_settings().secret_key, JWT_AUDIENCE)
    except pyjwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    with SessionLocal() as db:
        return db.get(User, int(user_id))


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: 403s (AppError) unless the logged-in user's role is one of
    `allowed_roles`. This is the sole source of authorization truth — a UI element hidden by
    role elsewhere in the app is convenience only, never a substitute for this check
    (docs/TASKS_PHASE6.md design decision 4).
    """

    def dependency(request: Request, user: User = Depends(current_active_user)) -> User:
        if user.role not in allowed_roles:
            raise AppError("forbidden", get_t(request)("errors.forbidden"), status_code=403)
        return user

    return dependency
