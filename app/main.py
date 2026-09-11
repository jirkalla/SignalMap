"""SignalMap FastAPI application entrypoint.

Phase 1 (thin vertical slice): client management, prompt management, and
manually-triggered runs against Google Gemini. See docs/REQUIREMENTS.md and
docs/TASKS.md for the full scope, and the signalmap-conventions skill for
project-wide conventions.
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import auth_backend, fastapi_users
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

app.include_router(clients.router)
app.include_router(prompt_sets.router)
app.include_router(prompts.router)
app.include_router(runs.router)
app.include_router(markets.router)
app.include_router(providers.router)
app.include_router(ai_models.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(help.router)
app.include_router(findings.router)
app.include_router(locale.router)


@app.get("/health", tags=["system"])
def health():
    """Liveness check for Docker/orchestration. No database access, returns immediately."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    """Redirect the app root to the clients list, the phase-1 home screen."""
    return RedirectResponse(url="/clients")
