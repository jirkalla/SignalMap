"""SignalMap FastAPI application entrypoint.

Phase 1 (thin vertical slice): client management, prompt management, and
manually-triggered runs against Google Gemini. See docs/REQUIREMENTS.md and
docs/TASKS.md for the full scope, and the signalmap-conventions skill for
project-wide conventions.
"""

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi_users.password import PasswordHelper
from sqlalchemy.orm import Session

from app.auth import auth_backend, current_active_user, current_user_from_cookie, fastapi_users, require_role
from app.database import get_db
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.models import User
from app.routers import (
    account,
    ai_models,
    clients,
    dashboard,
    findings,
    help,
    locale,
    markets,
    personas,
    prompt_sets,
    prompts,
    providers,
    runs,
    settings,
    users,
)
from app.templating import get_t, render

# Exempt from the must-change-password redirect below: the login/logout flow itself (or a
# stuck account could never log out of its own forced state), the change-password page and its
# own POST target, the locale switch (must keep working everywhere, including here), and the
# liveness check (no human involved).
_PASSWORD_CHANGE_EXEMPT_PATHS = {"/login", "/auth/login", "/auth/logout", "/change-password", "/health"}

configure_logging()

app = FastAPI(
    title="SignalMap",
    description=(
        "AI corporate-perception intelligence — phase 1 vertical slice. "
        "Configure a client, define a prompt, run it against Google Gemini, "
        "and inspect the stored raw answer and citations."
    ),
    version="0.1.0",
    # The built-in /docs, /redoc, and /openapi.json are unauthenticated by default — disabled
    # here and replaced below with admin-only versions (docs/TASKS_PHASE6.md follow-up: hiding
    # the nav link alone left the OpenAPI schema, which exposes every route/model in the app,
    # reachable by anyone logged in just by typing the URL).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

register_exception_handlers(app)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse | RedirectResponse:
    """Turn fastapi-users' HTTPException(401) into a redirect to /login for full-page
    navigations, so an unauthenticated request to any HTML route shows a login form instead of
    a raw JSON blob — verified against fastapi_users.authentication.authenticator, which raises
    plain HTTPException(401), not this project's own AppError (docs/TASKS_PHASE6.md design
    decision 6). The dashboard's own /api/* endpoints are fetched via JS, not navigated to
    directly, so they keep the plain JSON 401 a fetch() call can actually branch on.
    """
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and "/api/" not in request.url.path:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.middleware("http")
async def enforce_password_change(request: Request, call_next):
    """Redirect to /change-password on every request from a logged-in account that still has
    `must_change_password=True` — regardless of role or where the request was actually headed
    (docs/TASKS_PHASE6.md P6-T5). Runs as real ASGI middleware, not a router-level dependency,
    because it has to apply uniformly across every router (including the admin-only `users`
    router, set up with its own `require_role` gate) without editing each one individually.
    `current_user_from_cookie` returns None for an anonymous request, so this is a no-op for
    visitors who aren't logged in at all — the existing login gate still handles those.

    Only resolves the user (and stashes it on `request.state.current_user` for `app/templating.py`'s
    `render()` to reuse, avoiding a second redundant DB round-trip on the same request) on
    non-exempt paths — NOT unconditionally on every request. An earlier version of this fix did it
    unconditionally, which silently gave the exempt paths a database dependency they explicitly
    don't have: `/health`'s own docstring/test say "no database access", but `current_user_from_cookie`
    does a real sync DB query whenever a valid session cookie is present, regardless of path (found
    in a second round of code review, 2026-09-12 — reproduced against a real cookie hitting
    `/health`). Exempt paths that DO render a template while logged in anyway (`/login`,
    `/change-password`) fall back to `render()`'s own direct `current_user_from_cookie` call
    instead — one lookup on those two low-traffic pages, same as before this optimization existed,
    rather than a lookup on every single request including `/health`/`/auth/logout`/`/set-locale*`.
    """
    if request.url.path not in _PASSWORD_CHANGE_EXEMPT_PATHS and not request.url.path.startswith("/set-locale"):
        request.state.current_user = current_user_from_cookie(request)
        user = request.state.current_user
        if user is not None and user.must_change_password:
            return RedirectResponse(url="/change-password", status_code=status.HTTP_303_SEE_OTHER)
    return await call_next(request)


app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth", tags=["auth"])

# Already fully self-gated (require_role("admin") at the router level, or — for account —
# current_active_user with no role restriction) — registered on their own, not with the generic
# _login_required batch below, so there's exactly one place each expresses its access
# requirement instead of two overlapping ones (docs/TASKS_PHASE6.md P6-T6).
app.include_router(users.router)
app.include_router(providers.router)
app.include_router(ai_models.router)
app.include_router(settings.router)
app.include_router(account.router)

# Every router below requires a logged-in session (any role) except `locale` — the language
# switch must keep working even on the login page itself, before anyone is authenticated.
# Which specific actions need which role (admin/editor/viewer) is a separate, finer-grained
# layer (docs/TASKS_PHASE6.md P6-T6), applied per-route inside each router — this is only the
# outermost "are you logged in at all" gate.
_login_required = [Depends(current_active_user)]
app.include_router(clients.router, dependencies=_login_required)
app.include_router(prompt_sets.router, dependencies=_login_required)
app.include_router(prompts.router, dependencies=_login_required)
app.include_router(runs.router, dependencies=_login_required)
app.include_router(markets.router, dependencies=_login_required)
app.include_router(personas.router, dependencies=_login_required)
app.include_router(dashboard.router, dependencies=_login_required)
app.include_router(locale.router)

# Guide and Findings are hidden from viewer (docs/ROADMAP.md §1 follow-up) — not a
# create/edit/delete distinction like everywhere else in this file, just content viewer doesn't
# need; still enforced at the route, not just the nav link, for the same reason as /docs below.
_editor_or_admin = [Depends(require_role("admin", "editor"))]
app.include_router(help.router, dependencies=_editor_or_admin)
app.include_router(findings.router, dependencies=_editor_or_admin)


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_role("admin"))])
def get_openapi_schema():
    """The raw OpenAPI schema, admin-only — see the FastAPI(...) constructor above for why the
    built-in unauthenticated one is disabled.
    """
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_role("admin"))])
def get_docs():
    """Swagger UI, admin-only — see the FastAPI(...) constructor above."""
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


@app.get("/health", tags=["system"])
def health():
    """Liveness check for Docker/orchestration. No database access, returns immediately."""
    return {"status": "ok"}


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    """Render the login form. Public — no `current_active_user` dependency here, or an
    unauthenticated visit would redirect to itself forever.
    """
    return render(request, "auth/login.html")


@app.get("/change-password", include_in_schema=False)
def change_password_form(request: Request, user: User = Depends(current_active_user)):
    """Render the password-change form. Requires login (any role) — unlike /login, there's no
    redirect-loop risk here, since an unauthenticated visit is sent to /login, not back to
    itself.
    """
    return render(request, "auth/change_password.html")


@app.post("/change-password", include_in_schema=False)
def change_password(
    request: Request,
    current_password: str = Form(..., description="The account's current password, to prove this request isn't just a stolen session cookie."),
    new_password: str = Form(..., min_length=8, description="The account's new password."),
    current: User = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    """Set a new password for the logged-in account and clear `must_change_password`.

    Requires only being logged in, no specific role — every account goes through this after
    creation or an admin password reset (app/routers/users.py), regardless of role.

    Requires the account's current password before accepting a new one (found in code review,
    2026-09-12) — without this, anyone holding a valid session cookie (stolen via XSS, a shared
    device, a leaked proxy log) could silently take the account over permanently, with no
    re-authentication step and no notification path back to the real owner (this app has no email
    flow by design). `verify_and_update` is fastapi-users' own PasswordHelper API for checking a
    plaintext password against a stored hash.

    `current` (from `current_active_user`) is bound to the auth subsystem's async session
    (app/auth.py) — mutating it and committing `db` (a separate, synchronous session) would
    silently do nothing, since they aren't the same session. Re-fetching by id through the sync
    session first avoids that trap; only `current.id` is read off the async-bound object.
    """
    user = db.get(User, current.id)
    password_helper = PasswordHelper()
    verified, _ = password_helper.verify_and_update(current_password, user.hashed_password)
    if not verified:
        t = get_t(request)
        return render(
            request, "auth/change_password.html", {"error": t("errors.current_password_incorrect")}, status_code=400
        )
    user.hashed_password = password_helper.hash(new_password)
    user.must_change_password = False
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/", include_in_schema=False)
def root():
    """Redirect the app root to the clients list, the phase-1 home screen."""
    return RedirectResponse(url="/clients")
