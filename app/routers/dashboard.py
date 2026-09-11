"""Dashboard v0 — citation league table and time series (docs/TASKS_PHASE4.md).

Read-only: every endpoint here aggregates existing citations/runs/
raw_responses/analysis_results — no new analysis skill computes anything,
nothing is written. `own_domain_rate` is the one figure that reuses another
layer's output (the `mention_visibility` result from phase 3) rather than
recomputing domain matching itself — see design decision 6.

Every endpoint requires `client_id` (design decision 4 — dashboard v0 is
single-client scope, no cross-client aggregate view) and shares the same
optional `range`/`market_id`/`provider_id` filters via `_scoped_runs_query`.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    AIModel,
    AnalysisResult,
    AnalysisSkill,
    Citation,
    Client,
    Market,
    Prompt,
    PromptSet,
    Provider,
    RawResponse,
    Run,
)
from app.routers.clients import _get_client_or_404
from app.templating import render
from app.utils import normalize_domain

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DashboardRange = Literal["30d", "90d", "quarter", "all"]
DashboardMetric = Literal["citations", "runs", "own_rate"]

_CLIENT_ID_QUERY = Query(
    ..., description="Client to scope every dashboard figure to — required, dashboard v0 has no cross-client view."
)
_RANGE_QUERY: DashboardRange = Query(
    "90d",
    alias="range",
    description="Shorthand date window: 30d, 90d (rolling), quarter (start of the current calendar quarter to "
    "now), or all.",
)
_MARKET_ID_QUERY = Query(None, description="Restrict to runs targeting this market. Omitted: every market.")
_PROVIDER_ID_QUERY = Query(
    None, description="Restrict to runs against models from this provider. Omitted: every provider."
)


class DashboardSummary(BaseModel):
    """KPI totals for one client over the resolved date range."""

    runs_count: int = Field(..., description="Successful runs in range.")
    citations_count: int = Field(..., description="Total citations across those runs.")
    distinct_domains_count: int = Field(..., description="Distinct cited source domains (nulls excluded).")
    own_domain_rate: float | None = Field(
        ...,
        description="Share (0-100) of analyzed runs where the client's own domain was cited, per the "
        "mention_visibility result computed at run time. None when the client has no domain set, or no run "
        "in range has been analyzed yet.",
    )


class DomainRow(BaseModel):
    """One row of the cited-domains league table."""

    rank: int
    domain: str
    citations_count: int
    run_coverage_pct: float = Field(..., description="Share (0-100) of runs in range that cited this domain.")
    first_seen: date
    last_seen: date
    is_own_domain: bool = Field(
        ..., description="True when this domain is the client's own domain, exactly or as a subdomain."
    )


class WeekPoint(BaseModel):
    """One weekly bucket of a dashboard time series."""

    week_start: date
    value: float


class TimeseriesResponse(BaseModel):
    """A metric bucketed by calendar week, with no gaps — every week in range is present."""

    weeks: list[WeekPoint]


def _range_bounds(range_: DashboardRange) -> tuple[datetime | None, datetime | None]:
    """Translate a range shorthand into (date_from, date_to) bounds. `None, None` means "all time"."""
    now = datetime.now(timezone.utc)
    if range_ == "all":
        return None, None
    if range_ == "30d":
        return now - timedelta(days=30), now
    if range_ == "90d":
        return now - timedelta(days=90), now
    # "quarter": start of the current calendar quarter, not a rolling 90-day window — distinct from
    # "90d" even though the two are close in length most of the year.
    quarter_start_month = ((now.month - 1) // 3) * 3 + 1
    quarter_start = now.replace(month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return quarter_start, now


def _scoped_runs_query(
    client_id: int,
    date_from: datetime | None,
    date_to: datetime | None,
    market_id: int | None,
    provider_id: int | None,
) -> Select:
    """Base Select of successful `Run` rows belonging to one client, narrowed by the shared filter set.

    Every dashboard endpoint builds on this rather than repeating the client/date/market/provider
    scoping independently — callers reduce it to `Run.id` (`.with_only_columns(Run.id)`) and use it as
    a subquery for whatever GROUP BY/JOIN that endpoint needs on top (citations, analysis results, ...).
    Deliberately takes no `db` — building a `Select` needs no session, only `db.execute()`/`db.scalar()`
    at the call site does.
    """
    query = (
        select(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .where(PromptSet.client_id == client_id, Run.status == "success")
    )
    if date_from is not None:
        query = query.where(Run.started_at >= date_from)
    if date_to is not None:
        query = query.where(Run.started_at <= date_to)
    if market_id is not None:
        query = query.where(Run.market_id == market_id)
    if provider_id is not None:
        query = query.join(AIModel, Run.model_id == AIModel.id).where(AIModel.provider_id == provider_id)
    return query


def _week_starts(date_from: datetime, date_to: datetime) -> list[date]:
    """Every Monday-aligned week start between `date_from` and `date_to`, inclusive of both ends' weeks —
    the full x-axis a time series chart needs, independent of which weeks actually have data.
    """
    start = date_from.date() - timedelta(days=date_from.weekday())
    end = date_to.date() - timedelta(days=date_to.weekday())
    weeks = []
    current = start
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def _own_domain_rate(db: Session, client: Client, run_ids_query: Select) -> float | None:
    """Share (0-100) of runs in `run_ids_query` whose mention_visibility result has cited=true.

    None when the client has no domain set (citation-matching never ran for them, per phase 3 design
    decision 3) or when no run in scope has an mention_visibility result yet — a rate needs both a
    numerator and a meaningful denominator, not an arbitrary 0.
    """
    if not client.domain:
        return None

    base = (
        select(AnalysisResult.id)
        .join(RawResponse, AnalysisResult.raw_response_id == RawResponse.id)
        .join(AnalysisSkill, AnalysisResult.analysis_skill_id == AnalysisSkill.id)
        .where(AnalysisSkill.key == "mention_visibility", RawResponse.run_id.in_(run_ids_query))
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if not total:
        return None
    cited = db.scalar(
        select(func.count()).select_from(base.where(AnalysisResult.output["cited"].astext == "true").subquery())
    ) or 0
    return round(100 * cited / total, 1)


@router.get("", include_in_schema=False)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Dashboard v0 page shell.

    Renders only the client/market/provider option lists and the page frame — every KPI, the league
    table, and the time series are fetched client-side by the Vue island from the JSON endpoints
    below (design decision 10), not computed here.
    """
    clients = db.scalars(select(Client).order_by(Client.name)).all()
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    return render(
        request,
        "dashboard/index.html",
        {
            "dashboard_init": {
                "clients": [{"id": c.id, "name": c.name} for c in clients],
                "markets": [{"id": m.id, "label": m.locale_name or m.code} for m in markets],
                "providers": [{"id": p.id, "name": p.name} for p in providers],
            }
        },
    )


@router.get("/api/summary", response_model=DashboardSummary)
def dashboard_summary(
    request: Request,
    client_id: int = _CLIENT_ID_QUERY,
    range: DashboardRange = _RANGE_QUERY,
    market_id: int | None = _MARKET_ID_QUERY,
    provider_id: int | None = _PROVIDER_ID_QUERY,
    db: Session = Depends(get_db),
) -> DashboardSummary:
    """KPI totals for one client: run/citation counts, distinct domains, and own-domain citation rate.

    Backs the dashboard's KPI tiles. `own_domain_rate` reuses the phase 3 `mention_visibility` result
    rather than recomputing domain matching here — see docs/TASKS_PHASE4.md design decision 6.
    """
    client = _get_client_or_404(db, request, client_id)
    date_from, date_to = _range_bounds(range)
    runs_query = _scoped_runs_query(client_id, date_from, date_to, market_id, provider_id)
    run_ids_query = runs_query.with_only_columns(Run.id)

    runs_count = db.scalar(select(func.count()).select_from(runs_query.subquery())) or 0

    citations_base = (
        select(Citation).join(RawResponse, Citation.raw_response_id == RawResponse.id).where(
            RawResponse.run_id.in_(run_ids_query)
        )
    )
    citations_count = db.scalar(select(func.count()).select_from(citations_base.subquery())) or 0
    distinct_domains_count = db.scalar(
        select(func.count(func.distinct(Citation.source_domain)))
        .join(RawResponse, Citation.raw_response_id == RawResponse.id)
        .where(RawResponse.run_id.in_(run_ids_query), Citation.source_domain.is_not(None))
    ) or 0

    return DashboardSummary(
        runs_count=runs_count,
        citations_count=citations_count,
        distinct_domains_count=distinct_domains_count,
        own_domain_rate=_own_domain_rate(db, client, run_ids_query),
    )


@router.get("/api/domains", response_model=list[DomainRow])
def dashboard_domains(
    request: Request,
    client_id: int = _CLIENT_ID_QUERY,
    range: DashboardRange = _RANGE_QUERY,
    market_id: int | None = _MARKET_ID_QUERY,
    provider_id: int | None = _PROVIDER_ID_QUERY,
    limit: int = Query(10, ge=1, le=100, description="Maximum number of domains to return, ranked by citations."),
    db: Session = Depends(get_db),
) -> list[DomainRow]:
    """League table of domains cited across a client's runs, ranked by citation count.

    `is_own_domain` compares each cited domain against the client's own `domain` field (phase 3) via
    the shared `normalize_domain()` helper — exact match or subdomain (e.g. blog.acme.com matches
    acme.com). Domains never seen (no client domain set) all come back False, not an error.
    """
    client = _get_client_or_404(db, request, client_id)
    date_from, date_to = _range_bounds(range)
    runs_query = _scoped_runs_query(client_id, date_from, date_to, market_id, provider_id)
    run_ids_query = runs_query.with_only_columns(Run.id)
    total_runs = db.scalar(select(func.count()).select_from(runs_query.subquery())) or 0

    rows = db.execute(
        select(
            Citation.source_domain,
            func.count(Citation.id).label("citations_count"),
            func.count(func.distinct(Run.id)).label("run_count"),
            func.min(Run.started_at).label("first_seen"),
            func.max(Run.started_at).label("last_seen"),
        )
        .join(RawResponse, Citation.raw_response_id == RawResponse.id)
        .join(Run, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query), Citation.source_domain.is_not(None))
        .group_by(Citation.source_domain)
        .order_by(func.count(Citation.id).desc())
        .limit(limit)
    ).all()

    own_domain_norm = normalize_domain(client.domain) if client.domain else None

    result: list[DomainRow] = []
    for rank, row in enumerate(rows, start=1):
        domain_norm = normalize_domain(row.source_domain)
        is_own = own_domain_norm is not None and (
            domain_norm == own_domain_norm or domain_norm.endswith("." + own_domain_norm)
        )
        result.append(
            DomainRow(
                rank=rank,
                domain=row.source_domain,
                citations_count=row.citations_count,
                run_coverage_pct=round(100 * row.run_count / total_runs, 1) if total_runs else 0.0,
                first_seen=row.first_seen.date(),
                last_seen=row.last_seen.date(),
                is_own_domain=is_own,
            )
        )
    return result


@router.get("/api/timeseries", response_model=TimeseriesResponse)
def dashboard_timeseries(
    request: Request,
    client_id: int = _CLIENT_ID_QUERY,
    range: DashboardRange = _RANGE_QUERY,
    market_id: int | None = _MARKET_ID_QUERY,
    provider_id: int | None = _PROVIDER_ID_QUERY,
    metric: DashboardMetric = Query(
        "citations", description="Weekly metric to return: citations, runs, or own_rate (own-domain citation %)."
    ),
    db: Session = Depends(get_db),
) -> TimeseriesResponse:
    """Weekly time series for one client: citation volume, run volume, or own-domain citation rate.

    Buckets are calendar weeks (Monday-start). Every week between the resolved date bounds is present
    with value=0 when there is no data that week — never a gap, so a chart never has to guess whether a
    missing point means "no data" or "zero that week".
    """
    client = _get_client_or_404(db, request, client_id)
    date_from, date_to = _range_bounds(range)
    runs_query = _scoped_runs_query(client_id, date_from, date_to, market_id, provider_id)
    run_ids_query = runs_query.with_only_columns(Run.id)

    effective_from, effective_to = date_from, date_to
    if effective_from is None or effective_to is None:
        bounds = db.execute(select(func.min(Run.started_at), func.max(Run.started_at)).where(
            Run.id.in_(run_ids_query)
        )).one()
        if bounds[0] is None:
            return TimeseriesResponse(weeks=[])
        effective_from = effective_from or bounds[0]
        effective_to = effective_to or bounds[1]

    weeks = _week_starts(effective_from, effective_to)
    values = _weekly_values(db, run_ids_query, metric)

    return TimeseriesResponse(weeks=[WeekPoint(week_start=w, value=values.get(w, 0.0)) for w in weeks])


def _weekly_values(db: Session, run_ids_query: Select, metric: DashboardMetric) -> dict[date, float]:
    """{week_start: value} for the requested metric — only weeks with at least one row, the caller fills
    every other week in range with 0 (see `_week_starts`/`dashboard_timeseries`).
    """
    week_col = func.date_trunc("week", Run.started_at).label("week")

    if metric == "runs":
        rows = db.execute(select(week_col, func.count(Run.id)).where(Run.id.in_(run_ids_query)).group_by("week")).all()
        return {row[0].date(): float(row[1]) for row in rows}

    if metric == "citations":
        rows = db.execute(
            select(week_col, func.count(Citation.id))
            .join(RawResponse, Citation.raw_response_id == RawResponse.id)
            .join(Run, RawResponse.run_id == Run.id)
            .where(Run.id.in_(run_ids_query))
            .group_by("week")
        ).all()
        return {row[0].date(): float(row[1]) for row in rows}

    # own_rate
    rows = db.execute(
        select(
            week_col,
            func.count(AnalysisResult.id),
            func.count(AnalysisResult.id).filter(AnalysisResult.output["cited"].astext == "true"),
        )
        .join(RawResponse, AnalysisResult.raw_response_id == RawResponse.id)
        .join(Run, RawResponse.run_id == Run.id)
        .join(AnalysisSkill, AnalysisResult.analysis_skill_id == AnalysisSkill.id)
        .where(Run.id.in_(run_ids_query), AnalysisSkill.key == "mention_visibility")
        .group_by("week")
    ).all()
    return {row[0].date(): (round(100 * row[2] / row[1], 1) if row[1] else 0.0) for row in rows}
