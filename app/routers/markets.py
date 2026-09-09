"""Market CRUD: manage the language/country contexts a prompt can target.

Markets are pure data (no adapter behind them, unlike providers), so full
CRUD is safe here. Deleting a market in use by a prompt is blocked rather
than cascading — losing which market a historical prompt targeted would
contradict the project's evidence-retention stance (NFR-6).
"""

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Market, Prompt
from app.templating import get_t, render

router = APIRouter(prefix="/markets", tags=["markets"])

_LANGUAGE_RE = re.compile(r"^[a-z]{2}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _validate_iso_format(language: str, country: str, t) -> str | None:
    """Format-only check (not a real ISO-code lookup) — catches "Czech" instead of "CZ",

    not "is CZ a real country". Proportionate to a prototype's needs; see
    the Findings entry on why this matters for a future Claude adapter.
    """
    if not _LANGUAGE_RE.match(language):
        return t("errors.market_invalid_language")
    if country and not _COUNTRY_RE.match(country):
        return t("errors.market_invalid_country")
    return None


def _get_market_or_404(db: Session, request: Request, market_id: int) -> Market:
    market = db.get(Market, market_id)
    if market is None:
        raise AppError("market_not_found", get_t(request)("errors.market_not_found"), status_code=404)
    return market


def _market_rows(db: Session) -> list[tuple[Market, int]]:
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    usage = dict(db.execute(select(Prompt.market_id, func.count(Prompt.id)).group_by(Prompt.market_id)).all())
    return [(m, usage.get(m.id, 0)) for m in markets]


@router.get("")
def list_markets(request: Request, db: Session = Depends(get_db)):
    """List all markets with how many prompts currently target each one."""
    return render(request, "markets/list.html", {"rows": _market_rows(db)})


@router.get("/new")
def new_market_form(request: Request):
    """Render the empty market-creation form."""
    t = get_t(request)
    return render(
        request,
        "markets/form.html",
        {"title": t("market.create_title"), "action": "/markets", "cancel_url": "/markets", "market": None},
    )


@router.post("")
def create_market(
    request: Request,
    code: str = Form(..., description="Short unique code, e.g. 'de-DE'."),
    language: str = Form(..., description="2-letter ISO 639-1 language code, e.g. 'de'."),
    country: str = Form("", description="Optional 2-letter ISO 3166-1 alpha-2 country code, e.g. 'DE'."),
    label: str = Form("", description="Optional human-readable label."),
    db: Session = Depends(get_db),
):
    """Create a new market. Rejects a duplicate code or malformed language/country with an

    inline error, not a raw API error — these are ordinary form mistakes, not exceptional
    API failures. `language` is normalized to lowercase, `country` to uppercase before
    validation, so casing mistakes don't need a resubmit.
    """
    t = get_t(request)
    code = code.strip()
    language = language.strip().lower()
    country = country.strip().upper()
    label = label.strip()
    form_state = {"code": code, "language": language, "country": country, "label": label}

    error = _validate_iso_format(language, country, t)
    if error is None and db.scalar(select(Market).where(Market.code == code)) is not None:
        error = t("errors.market_code_conflict")
    if error:
        return render(
            request,
            "markets/form.html",
            {"title": t("market.create_title"), "action": "/markets", "cancel_url": "/markets", "market": form_state, "error": error},
            status_code=409,
        )

    market = Market(code=code, language=language, country=country or None, label=label or None)
    db.add(market)
    db.commit()
    return RedirectResponse(url="/markets", status_code=303)


@router.get("/{market_id}/edit")
def edit_market_form(request: Request, market_id: int, db: Session = Depends(get_db)):
    """Render the market edit form, pre-filled with current values."""
    market = _get_market_or_404(db, request, market_id)
    t = get_t(request)
    return render(
        request,
        "markets/form.html",
        {"title": t("market.edit_title"), "action": f"/markets/{market_id}/edit", "cancel_url": "/markets", "market": market},
    )


@router.post("/{market_id}/edit")
def update_market(
    request: Request,
    market_id: int,
    code: str = Form(..., description="Short unique code, e.g. 'de-DE'."),
    language: str = Form(..., description="2-letter ISO 639-1 language code, e.g. 'de'."),
    country: str = Form("", description="Optional 2-letter ISO 3166-1 alpha-2 country code, e.g. 'DE'."),
    label: str = Form("", description="Optional human-readable label."),
    db: Session = Depends(get_db),
):
    """Update a market. Existing prompts keep referencing it by id, so editing code/label is always safe."""
    t = get_t(request)
    market = _get_market_or_404(db, request, market_id)
    code = code.strip()
    language = language.strip().lower()
    country = country.strip().upper()
    label = label.strip()
    form_state = {"code": code, "language": language, "country": country, "label": label}

    error = _validate_iso_format(language, country, t)
    if error is None:
        conflict = db.scalar(select(Market).where(Market.code == code, Market.id != market_id))
        if conflict is not None:
            error = t("errors.market_code_conflict")
    if error:
        return render(
            request,
            "markets/form.html",
            {
                "title": t("market.edit_title"),
                "action": f"/markets/{market_id}/edit",
                "cancel_url": "/markets",
                "market": form_state,
                "error": error,
            },
            status_code=409,
        )

    market.code = code
    market.language = language
    market.country = country or None
    market.label = label or None
    db.commit()
    return RedirectResponse(url="/markets", status_code=303)


@router.post("/{market_id}/delete")
def delete_market(request: Request, market_id: int, db: Session = Depends(get_db)):
    """Delete a market — blocked (inline error, not a raw API error) if any prompt still references it."""
    t = get_t(request)
    market = _get_market_or_404(db, request, market_id)
    in_use = db.scalar(select(func.count(Prompt.id)).where(Prompt.market_id == market_id))
    if in_use:
        return render(
            request,
            "markets/list.html",
            {"rows": _market_rows(db), "error": t("errors.market_in_use").format(count=in_use)},
            status_code=409,
        )
    db.delete(market)
    db.commit()
    return RedirectResponse(url="/markets", status_code=303)
