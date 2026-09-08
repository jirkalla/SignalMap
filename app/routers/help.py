"""In-app usage guide — how to operate SignalMap once it's already running.

Deliberately does not cover setup/Docker steps: you can't read an in-app
page before the app is running, so that content stays in docs/TASKS.md.
"""

from fastapi import APIRouter, Request

from app.templating import render

router = APIRouter(tags=["help"])


@router.get("/help")
def help_page(request: Request):
    """Render the bilingual usage guide: clients, prompt sets, prompts, runs."""
    return render(request, "help.html", {})
