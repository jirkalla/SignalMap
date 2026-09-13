"""Small helpers shared across routers."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market, Persona


def market_options(db: Session) -> list[tuple[int, str]]:
    """(id, display label) pairs for every market, sorted by code.

    Shared by every form that offers a market <select> (prompt creation,
    prompt editing, run trigger) so the label format stays consistent.
    """
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    return [(m.id, f"{m.code} — {m.locale_name}" if m.locale_name else m.code) for m in markets]


def persona_options(db: Session) -> list[tuple[int, str]]:
    """(id, label) pairs for every persona, sorted by label — shared by the run-trigger form
    (app/templates/prompts/detail.html)."""
    personas = db.scalars(select(Persona).order_by(Persona.label)).all()
    return [(p.id, p.label) for p in personas]


def default_persona_id(db: Session) -> int:
    """The id of the persona currently marked `is_default` — used to pre-select the run-trigger
    form's persona <select>. Always exists: `personas` can never end up with zero default rows
    (app/routers/personas.py enforces that on both delete and edit)."""
    return db.scalar(select(Persona.id).where(Persona.is_default.is_(True)))


def normalize_domain(domain: str) -> str:
    """Lowercase, strip a leading 'www.' — the shared form used to compare domains."""
    domain = domain.strip().lower()
    if domain.startswith("www."):
        domain = domain[len("www.") :]
    return domain


def is_own_domain(candidate_domain: str | None, client_domain: str | None) -> bool:
    """True when `candidate_domain` is the client's own domain, exactly or as a subdomain
    (e.g. blog.acme.com matches acme.com).

    The one comparison rule shared by the `mention_visibility` analysis skill (app/analysis/
    mention_visibility.py, matching a client's own domain against citation sources) and the
    phase 4 dashboard's league table (flagging which cited domain is the client's own) — not
    two independently-maintained copies of the same predicate.

    False whenever either domain is missing — a client with no `domain` set has nothing to
    compare against, and an empty citation source_domain can't match anything.
    """
    if not candidate_domain or not client_domain:
        return False
    candidate_norm = normalize_domain(candidate_domain)
    client_norm = normalize_domain(client_domain)
    return candidate_norm == client_norm or candidate_norm.endswith("." + client_norm)


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
