"""Market CRUD: manage the language/country contexts a prompt can target.

Markets are pure data (no adapter behind them, unlike providers), so full
CRUD is safe here. Deleting a market in use by a prompt is blocked rather
than cascading — losing which market a historical prompt targeted would
contradict the project's evidence-retention stance (NFR-6).
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import Market, Prompt
from app.templating import get_t, render

router = APIRouter(prefix="/markets", tags=["markets"])


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
    language: str = Form(..., description="Language code, e.g. 'de'."),
    country: str = Form("", description="Optional country code, e.g. 'DE'."),
    label: str = Form("", description="Optional human-readable label."),
    db: Session = Depends(get_db),
):
    """Create a new market. Rejects a duplicate code with an inline error, not a raw API error —

    picking an existing code is an ordinary form mistake, not an exceptional API failure.
    """
    t = get_t(request)
    code = code.strip()
    if db.scalar(select(Market).where(Market.code == code)) is not None:
        return render(
            request,
            "markets/form.html",
            {
                "title": t("market.create_title"),
                "action": "/markets",
                "cancel_url": "/markets",
                "market": {"code": code, "language": language, "country": country, "label": label},
                "error": t("errors.market_code_conflict"),
            },
            status_code=409,
        )
    market = Market(code=code, language=language.strip(), country=country.strip() or None, label=label.strip() or None)
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
    language: str = Form(..., description="Language code, e.g. 'de'."),
    country: str = Form("", description="Optional country code, e.g. 'DE'."),
    label: str = Form("", description="Optional human-readable label."),
    db: Session = Depends(get_db),
):
    """Update a market. Existing prompts keep referencing it by id, so editing code/label is always safe."""
    t = get_t(request)
    market = _get_market_or_404(db, request, market_id)
    code = code.strip()
    conflict = db.scalar(select(Market).where(Market.code == code, Market.id != market_id))
    if conflict is not None:
        return render(
            request,
            "markets/form.html",
            {
                "title": t("market.edit_title"),
                "action": f"/markets/{market_id}/edit",
                "cancel_url": "/markets",
                "market": {"code": code, "language": language, "country": country, "label": label},
                "error": t("errors.market_code_conflict"),
            },
            status_code=409,
        )
    market.code = code
    market.language = language.strip()
    market.country = country.strip() or None
    market.label = label.strip() or None
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
