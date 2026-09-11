"""SignalMap FastAPI application entrypoint.

Phase 1 (thin vertical slice): client management, prompt management, and
manually-triggered runs against Google Gemini. See docs/REQUIREMENTS.md and
docs/TASKS.md for the full scope, and the signalmap-conventions skill for
project-wide conventions.
"""

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import auth_backend, current_active_user, fastapi_users
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.routers import (
    ai_models,
    clients,
    dashboard,
    findings,
    help,
    locale,
    markets,
    prompt_sets,
    prompts,
    providers,
    runs,
    settings,
)
from app.templating import render

configure_logging()

app = FastAPI(
    title="SignalMap",
    description=(
        "AI corporate-perception intelligence — phase 1 vertical slice. "
        "Configure a client, define a prompt, run it against Google Gemini, "
        "and inspect the stored raw answer and citations."
    ),
    version="0.1.0",
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


app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth", tags=["auth"])

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
app.include_router(providers.router, dependencies=_login_required)
app.include_router(ai_models.router, dependencies=_login_required)
app.include_router(dashboard.router, dependencies=_login_required)
app.include_router(settings.router, dependencies=_login_required)
app.include_router(help.router, dependencies=_login_required)
app.include_router(findings.router, dependencies=_login_required)
app.include_router(locale.router)


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


@app.get("/", include_in_schema=False)
def root():
    """Redirect the app root to the clients list, the phase-1 home screen."""
    return RedirectResponse(url="/clients")
