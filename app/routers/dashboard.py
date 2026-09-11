"""Dashboard v0 — citation league table and time series (docs/TASKS_PHASE4.md).

HTTP layer only: parses/validates query params and delegates every aggregation to
app/services/dashboard.py, wrapping the result in this module's Pydantic response models (kept
here per design decision 8 — colocated with the routes, not moved into the service module).

Every JSON endpoint requires `client_id` (design decision 4 — dashboard v0 is single-client
scope, no cross-client aggregate view) and shares the same optional `range`/`market_id`/
`provider_id` filters, resolved once per request by the `_dashboard_scope` dependency below.
"""

from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, Market, Provider
from app.routers.clients import _get_client_or_404
from app.services import dashboard as dashboard_service
from app.services.dashboard import DashboardMetric, DashboardRange
from app.templating import render

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

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


@dataclass
class DashboardScope:
    """The resolved client + filters shared by every JSON endpoint below, built once via the
    `_dashboard_scope` dependency instead of each route repeating client lookup, date-range
    resolution, and scoped-query construction — a new shared filter only needs adding here.
    """

    client: Client
    run_ids_query: Select
    date_from: datetime | None
    date_to: datetime | None


def _dashboard_scope(
    request: Request,
    client_id: int = _CLIENT_ID_QUERY,
    range: DashboardRange = _RANGE_QUERY,
    market_id: int | None = _MARKET_ID_QUERY,
    provider_id: int | None = _PROVIDER_ID_QUERY,
    db: Session = Depends(get_db),
) -> DashboardScope:
    """Resolve `client_id` (404 if unknown) and the shared client/date/market/provider filter set
    into one `DashboardScope`, reused by every JSON endpoint via FastAPI's per-request dependency
    caching (this runs once per request even though multiple things depend on it).
    """
    client = _get_client_or_404(db, request, client_id)
    date_from, date_to = dashboard_service.range_bounds(range)
    run_ids_query = dashboard_service.scoped_run_ids_query(client_id, date_from, date_to, market_id, provider_id)
    return DashboardScope(client=client, run_ids_query=run_ids_query, date_from=date_from, date_to=date_to)


@router.get("", include_in_schema=False)
def dashboard_page(
    request: Request,
    client_id: int | None = Query(
        None,
        description="Client to preselect on load, e.g. when linked from that client's own detail page. "
        "Falls back to the first client (alphabetically) when omitted or when it doesn't match any client.",
    ),
    db: Session = Depends(get_db),
):
    """Dashboard v0 page shell.

    Renders only the client/market/provider option lists and the page frame — every KPI, the league
    table, and the time series are fetched client-side by the Vue island from the JSON endpoints
    below (design decision 10), not computed here.
    """
    clients = db.scalars(select(Client).order_by(Client.name)).all()
    markets = db.scalars(select(Market).order_by(Market.code)).all()
    providers = db.scalars(select(Provider).order_by(Provider.name)).all()
    valid_client_id = client_id if any(c.id == client_id for c in clients) else None
    return render(
        request,
        "dashboard/index.html",
        {
            "dashboard_init": {
                "clients": [{"id": c.id, "name": c.name, "domain": c.domain} for c in clients],
                "markets": [{"id": m.id, "label": m.locale_name or m.code} for m in markets],
                "providers": [{"id": p.id, "name": p.name} for p in providers],
                "initial_client_id": valid_client_id,
            }
        },
    )


@router.get("/api/summary", response_model=DashboardSummary)
def dashboard_summary(scope: DashboardScope = Depends(_dashboard_scope), db: Session = Depends(get_db)) -> DashboardSummary:
    """KPI totals for one client: run/citation counts, distinct domains, and own-domain citation rate.

    Backs the dashboard's KPI tiles. `own_domain_rate` reuses the phase 3 `mention_visibility` result
    rather than recomputing domain matching here — see docs/TASKS_PHASE4.md design decision 6.
    """
    runs_count = dashboard_service.count_runs(db, scope.run_ids_query)
    citations_count, distinct_domains_count = dashboard_service.citation_totals(db, scope.run_ids_query)
    return DashboardSummary(
        runs_count=runs_count,
        citations_count=citations_count,
        distinct_domains_count=distinct_domains_count,
        own_domain_rate=dashboard_service.own_domain_rate(db, scope.client, scope.run_ids_query),
    )


@router.get("/api/domains", response_model=list[DomainRow])
def dashboard_domains(
    scope: DashboardScope = Depends(_dashboard_scope),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of domains to return, ranked by citations."),
    db: Session = Depends(get_db),
) -> list[DomainRow]:
    """League table of domains cited across a client's runs, ranked by citation count.

    `is_own_domain` compares each cited domain against the client's own `domain` field (phase 3) via
    the shared `app.utils.is_own_domain()` helper — exact match or subdomain (e.g. blog.acme.com
    matches acme.com). Domains never seen (no client domain set) all come back False, not an error.
    """
    rows = dashboard_service.domain_league_rows(db, scope.client, scope.run_ids_query, limit)
    return [DomainRow(**vars(row)) for row in rows]


@router.get("/api/timeseries", response_model=TimeseriesResponse)
def dashboard_timeseries(
    scope: DashboardScope = Depends(_dashboard_scope),
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
    weeks = dashboard_service.resolve_week_range(db, scope.run_ids_query, scope.date_from, scope.date_to)
    values = dashboard_service.weekly_values(db, scope.run_ids_query, metric)
    return TimeseriesResponse(weeks=[WeekPoint(week_start=w, value=values.get(w, 0.0)) for w in weeks])
