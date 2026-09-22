"""Small helpers shared across routers."""

import re

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.models import Market, Persona, Prompt


def market_options(db: Session) -> list[tuple[int, str]]:
    """(id, display label) pairs for every market, sorted by code.

    Shared by every form that offers a market <select> (prompt creation,
    prompt editing, run trigger) so the label format stays consistent.
    """
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    return [(m.id, f"{m.code} — {m.locale_name}" if m.locale_name else m.code) for m in markets]


def current_prompt_version(db: Session, root_prompt_id: int) -> Prompt:
    """The current version of a prompt lineage rooted at `root_prompt_id`.

    Shared by app/services/queue.py's ticker (`enqueue_due_schedules`) and
    app/routers/schedules.py (the schedule form and its cost/occurrence preview) — both need
    the same "resolve a schedule's stored lineage root to the concrete Prompt version that would
    actually run today" step (docs/TASKS_SCHEDULER.md design decision 6), which is exactly what
    editing a prompt is designed to keep separate from a schedule ever pointing at stale text.
    """
    return db.scalar(
        select(Prompt).where(
            or_(Prompt.id == root_prompt_id, Prompt.root_prompt_id == root_prompt_id),
            Prompt.is_current_version.is_(True),
        )
    )


def prompt_lineage_ids(db: Session, root_prompt_id: int) -> list[int]:
    """Every Prompt id in a lineage rooted at `root_prompt_id` — same `WHERE id = root_id OR
    root_prompt_id = root_id` shape as app/services/export.py, reused wherever a query needs to
    span every version's runs, not just the current one (e.g. a schedule's historical cost
    estimate in app/services/cost.py, which would otherwise see zero history right after a
    prompt edit creates a new current version with no runs of its own yet).
    """
    return db.scalars(
        select(Prompt.id).where(or_(Prompt.id == root_prompt_id, Prompt.root_prompt_id == root_prompt_id))
    ).all()


def most_recent_prompt_market_id(db: Session, prompt_set_id: int) -> int | None:
    """Market of the most recently created prompt in this set — used as the bulk-import form's
    smart default, so re-importing into an existing (typically single-language) prompt set
    doesn't force re-picking the same market every time. None for a brand-new, empty prompt set.
    """
    return db.scalar(
        select(Prompt.market_id).where(Prompt.prompt_set_id == prompt_set_id).order_by(Prompt.created_at.desc()).limit(1)
    )


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


def normalized_domain_sql(col: ColumnElement) -> ColumnElement:
    """SQL-side twin of `normalize_domain`, for use inside `GROUP BY`/`COUNT(DISTINCT ...)`
    (docs/TASKS_PRE_SCHEDULER.md PRE-4, design decision 11) — the same dual-implementation shape
    `app.services.cost.estimate_run_cost`/`run_cost_sql_expr` already uses for the same reason:
    aggregation has to happen in SQL (see `domain_league_rows`'s docstring for why grouping in
    Python after the query breaks `run_coverage_pct` and `LIMIT`), but a citation is read one row
    at a time everywhere else in this codebase (`is_own_domain`, the mention_visibility skill,
    `/api/classify`), where the plain Python function is the natural fit.

    Kept in sync with `normalize_domain` by `tests/test_dashboard.py`'s table test, run against
    real Postgres — not by hand — since the two are independent expressions of one rule and a
    future edit to one that misses the other would silently split `meag.com` back into two rows
    without any test noticing until someone reads production data again (found 2026-09-18: 26
    domains split this way, 312 citations affected).

    Deliberately no broader than what production data actually needed: lowercase + strip a leading
    'www.' only. No uppercase, port or trailing-dot variants were found among the 26 split domains,
    so a more elaborate rule here would be design-for-hypothetical-data (AI_INSTRUCTIONS §5), not a
    fix for anything observed.
    """
    return func.regexp_replace(func.lower(func.trim(col)), r"^www\.", "")


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
