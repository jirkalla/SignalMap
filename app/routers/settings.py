"""Settings: per-provider system_instruction templates (app/models/settings.py).

Only providers without real geographic/language API targeting need this —
today that's Google Gemini. A template is validated (dry-run formatted)
before saving, so a typo'd placeholder can't silently break future runs.
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
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    templates = {row.provider_id: row.template for row in db.scalars(select(SystemInstructionTemplate)).all()}
    return [(p, templates.get(p.id, "")) for p in providers]


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
