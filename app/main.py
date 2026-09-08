"""SignalMap FastAPI application entrypoint.

Phase 1 (thin vertical slice): client management, prompt management, and
manually-triggered runs against Google Gemini. See docs/REQUIREMENTS.md and
docs/TASKS.md for the full scope, and the signalmap-conventions skill for
project-wide conventions.
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.errors import register_exception_handlers
from app.routers import clients, help, locale, markets, prompt_sets, prompts, runs, settings

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

app.include_router(clients.router)
app.include_router(prompt_sets.router)
app.include_router(prompts.router)
app.include_router(runs.router)
app.include_router(markets.router)
app.include_router(settings.router)
app.include_router(help.router)
app.include_router(locale.router)


@app.get("/health", tags=["system"])
def health():
    """Liveness check for Docker/orchestration. No database access, returns immediately."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    """Redirect the app root to the clients list, the phase-1 home screen."""
    return RedirectResponse(url="/clients")
