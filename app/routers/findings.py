"""Findings: a running technical log of things discovered during development

(API limitations, cost/compliance notes, verified behavior) — not a user
operating guide (that's /help). Static content, English only: this is
developer/product reference material, not day-to-day analyst UI text, so
it deliberately does not go through the t() mechanism. New entries are
added directly to app/templates/findings.html as they come up, the same
way docs/REQUIREMENTS.md's "Phase 1 amendments" section is maintained —
no database, no admin form, just a page one edits by hand.
"""

from fastapi import APIRouter, Request

from app.templating import render

router = APIRouter(tags=["findings"])


@router.get("/findings")
def findings_page(request: Request):
    """Render the findings log."""
    return render(request, "findings.html", {})
