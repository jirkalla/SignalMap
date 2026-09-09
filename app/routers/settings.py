"""Settings: per-provider system_instruction templates (app/models/settings.py).

Every provider gets this — it's the only lever any provider's API exposes
for the answer's *language*. It is not the only lever for search *location*
though: a provider whose API takes real geographic search targeting (e.g.
Anthropic's `web_search` user_location, app/routers/runs.py's
`market_country`) should get a shorter, language-only template here rather
than the fuller location-simulating default below, since real targeting
already covers that half. See docs/TASKS_PHASE2.md P2-T4 follow-up
(2026-09-09) for the design decision behind this split. A template is
validated (dry-run formatted) before saving, so a typo'd placeholder can't
silently break future runs.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Provider, SystemInstructionTemplate
from app.templating import get_t, render

router = APIRouter(prefix="/settings", tags=["settings"])

# Sensible zero-config default for a provider with no saved row yet (e.g.
# right after its seed migration, before anyone visits this page) — covers
# both language and location-simulation, for a provider with no real
# geographic API targeting of its own. See app/routers/runs.py's
# _market_system_instruction for where this is actually applied, and
# _rows() below for why it's shown here rather than hidden behind a blank
# textarea (the UI must always show what's really being sent).
DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE = (
    "The person asking this question is located in {market_locale_name} and writing in "
    "{market_language}. Answer in {market_language}, using regional context and examples "
    "relevant there where applicable."
)

_DRY_RUN_VALUES = {
    "market_code": "cs-CZ",
    "market_language": "cs",
    "market_country": "CZ",
    "market_locale_name": "Czech (Czech Republic)",
}


def _validate_template(template: str) -> str | None:
    """Return an error message if `template` uses an unknown placeholder, else None."""
    if not template.strip():
        return None
    try:
        template.format(**_DRY_RUN_VALUES)
    except (KeyError, IndexError) as exc:
        return f"Unknown placeholder: {exc}"
    return None


def _rows(db: Session) -> list[tuple[Provider, str]]:
    """Every provider with the template text actually in effect for it right now.

    A provider with no saved row shows DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE
    here, not a blank box — that default is what _market_system_instruction
    (app/routers/runs.py) silently falls back to for such a provider, and
    this page must never show something different from what a run actually
    sends. A saved row with empty text still shows blank (that's the
    explicit "send nothing" choice, not the unconfigured state).
    """
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    templates = {row.provider_id: row.template for row in db.scalars(select(SystemInstructionTemplate)).all()}
    return [(p, templates.get(p.id, DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE)) for p in providers]


@router.get("")
def settings_page(request: Request, db: Session = Depends(get_db)):
    """Show one editable system-instruction template per provider."""
    return render(request, "settings.html", {"rows": _rows(db)})


@router.post("/{provider_id}")
def update_template(
    request: Request,
    provider_id: int,
    template: str = Form(
        "", description="System-instruction hint template for this provider. Empty means none is sent."
    ),
    db: Session = Depends(get_db),
):
    """Save a provider's system-instruction template.

    Validated (dry-run formatted with sample values) before saving, so a
    bad placeholder shows an inline error instead of silently breaking the
    next run that uses it.
    """
    t = get_t(request)
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise AppError("provider_not_found", t("errors.provider_not_found"), status_code=404)

    error = _validate_template(template)
    if error:
        rows = [(p, template if p.id == provider_id else existing) for p, existing in _rows(db)]
        return render(
            request, "settings.html", {"rows": rows, "error": f"{provider.name}: {error}"}, status_code=400
        )

    row = db.scalar(select(SystemInstructionTemplate).where(SystemInstructionTemplate.provider_id == provider_id))
    stripped = template.strip()
    if row is None:
        db.add(SystemInstructionTemplate(provider_id=provider_id, template=stripped))
    else:
        row.template = stripped
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)
