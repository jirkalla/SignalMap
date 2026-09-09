"""Small helpers shared across routers."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market


def market_options(db: Session) -> list[tuple[int, str]]:
    """(id, display label) pairs for every market, sorted by code.

    Shared by every form that offers a market <select> (prompt creation,
    prompt editing, run trigger) so the label format stays consistent.
    """
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    return [(m.id, f"{m.code} — {m.locale_name}" if m.locale_name else m.code) for m in markets]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "client"


def unique_slugify(db: Session, model: type, text: str, slug_column: str = "slug") -> str:
    """Slugify `text` and append a numeric suffix if the slug already exists.

    Used on client creation only — the slug is never regenerated on edit.
    """
    base_slug = _slugify(text)
    slug = base_slug
    suffix = 2
    column = getattr(model, slug_column)
    while db.scalar(select(model).where(column == slug)) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug
