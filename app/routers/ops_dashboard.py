"""Ops Dashboard JSON API (docs/TASKS_OPS_DASHBOARD.md T2) — internal engineering/ops visibility
into cost, latency, and errors across every client, never a client-facing report (design decision
1). Entirely separate from app/routers/dashboard.py: different audience, different access-control
boundary (admin/editor only, never viewer — enforced here, not just hidden in the UI), and every
aggregation scopes over all runs regardless of status, not just successful ones.

Every endpoint takes the same optional `range`/`client_id`/`prompt_set_id`/`prompt_id`/`user_id`
filter set, resolved once per request by the `_ops_scope` dependency below, mirroring
app/routers/dashboard.py's `_dashboard_scope` pattern.
"""

from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.errors import AppError
from app.routers.clients import _get_client_or_404
from app.routers.prompt_sets import _get_prompt_set_or_404
from app.routers.prompts import _get_prompt_or_404
from app.routers.users import _get_user_or_404
from app.services import ops_dashboard as ops_service
from app.services.date_ranges import DashboardRange, range_bounds
from app.templating import get_t, render

router = APIRouter(prefix="/ops", tags=["ops_dashboard"], dependencies=[Depends(require_role("admin", "editor"))])

_RANGE_QUERY: DashboardRange = Query(
    "30d", alias="range", description="Shorthand date window: 7d, 30d, 90d, quarter, or all (rolling except quarter)."
)
_CLIENT_ID_QUERY = Query(None, description="Restrict to one client's runs. Omitted: every client.")
_PROMPT_SET_ID_QUERY = Query(None, description="Restrict to one prompt set's runs. Omitted: every prompt set.")
_PROMPT_ID_QUERY = Query(None, description="Restrict to one prompt's runs. Omitted: every prompt.")
_USER_ID_QUERY = Query(
    None,
    description='Restrict to one user\'s runs: a numeric user id, or the literal "scheduler" for '
    "scheduler-triggered runs (trigger_type='scheduled' with no human behind them). Omitted: every user.",
)


@router.get("", include_in_schema=False)
def ops_page(request: Request):
    """Ops dashboard page shell (docs/TASKS_OPS_DASHBOARD.md T3).

    Thin on purpose: unlike app/routers/dashboard.py's page shell, there's no required top-level
    selector to pre-populate server-side — the global view fetches its own client/user tables from
    the JSON endpoints below via the Vue island, the same "server renders the frame, JS fetches the
    data" split as the client-facing dashboard (design decision 10 there). Access control is
    already covered by this router's own `require_role("admin", "editor")` dependency — a viewer
    hitting this route gets the same HTML 403 page as any other admin-only page route
    (app/errors.py's handler branches on "/api/" in the path, not on route-by-route special-casing).
    """
    return render(request, "ops/index.html")


def _resolve_user_filter(request: Request, user_id: str | None) -> tuple[int | None, bool]:
    """Parse the `user_id` query param into (resolved_user_id, is_scheduler) for
    `ops_scoped_run_ids_query`. A 400, not a 404 — this validates the *shape* of the filter value,
    not whether a specific user exists (an unknown numeric id is just a filter that matches
    nothing, same as any other optional filter elsewhere in this API).
    """
    if user_id is None:
        return None, False
    if user_id == "scheduler":
        return None, True
    if user_id.isdigit():
        return int(user_id), False
    raise AppError("invalid_user_id", get_t(request)("errors.invalid_user_id"), status_code=400)


@dataclass
class OpsScope:
    """The resolved filter set shared by every JSON endpoint below, built once via `_ops_scope`."""

    run_ids_query: Select
    date_from: datetime | None
    date_to: datetime | None
    range_: DashboardRange
    client_id: int | None
    prompt_set_id: int | None
    prompt_id: int | None


def _ops_scope(
    request: Request,
    range: DashboardRange = _RANGE_QUERY,
    client_id: int | None = _CLIENT_ID_QUERY,
    prompt_set_id: int | None = _PROMPT_SET_ID_QUERY,
    prompt_id: int | None = _PROMPT_ID_QUERY,
    user_id: str | None = _USER_ID_QUERY,
    db: Session = Depends(get_db),
) -> OpsScope:
    """Resolve the shared range/client/prompt-set/prompt/user filter set into one `OpsScope`.

    Filters here are optional narrowing, not identifying resources — an unknown `client_id`/
    `prompt_set_id`/`prompt_id` simply matches zero runs rather than 404ing, the same way
    `app/routers/dashboard.py`'s optional `market_id`/`provider_id` behave. The identifying-resource
    endpoints below (`prompt-detail`, `user-detail`, and the `client_id`/`prompt_set_id` required by
    `prompt-sets`/`prompts`) validate existence themselves before calling into this dependency.
    """
    date_from, date_to = range_bounds(range)
    resolved_user_id, is_scheduler = _resolve_user_filter(request, user_id)
    run_ids_query = ops_service.ops_scoped_run_ids_query(
        date_from, date_to, client_id, prompt_set_id, prompt_id, resolved_user_id, is_scheduler
    )
    return OpsScope(
        run_ids_query=run_ids_query,
        date_from=date_from,
        date_to=date_to,
        range_=range,
        client_id=client_id,
        prompt_set_id=prompt_set_id,
        prompt_id=prompt_id,
    )


class OpsSummaryResponse(BaseModel):
    """KPI totals for the resolved scope."""

    runs_count: int = Field(..., description="Finished runs in scope (success + error; pending runs are excluded).")
    success_count: int
    error_count: int
    success_rate_pct: float | None = Field(..., description="0-100. None when runs_count is 0.")
    total_cost_usd: float | None = Field(..., description="None when no run in scope has a computable cost.")
    avg_latency_ms: float | None = Field(..., description="None when no finished run in scope has a recorded latency.")


@router.get("/api/summary", response_model=OpsSummaryResponse)
def ops_summary(scope: OpsScope = Depends(_ops_scope), db: Session = Depends(get_db)) -> OpsSummaryResponse:
    """KPI totals (run/success/error counts, success rate, total cost, average latency) for the
    resolved filter set. Backs the ops dashboard's KPI tiles.
    """
    summary = ops_service.ops_summary(db, scope.run_ids_query)
    return OpsSummaryResponse(**vars(summary))


class OpsDailyPoint(BaseModel):
    bucket_start: date
    success_count: int
    error_count: int


class OpsDailyResponse(BaseModel):
    """A success/error time series, bucketed by day/week/month depending on `range` (design
    decision 5). Every bucket in range is present, even with zero runs — never a gap.
    """

    granularity: str = Field(..., description="One of: day, week, month.")
    points: list[OpsDailyPoint]


@router.get("/api/daily", response_model=OpsDailyResponse)
def ops_daily(scope: OpsScope = Depends(_ops_scope), db: Session = Depends(get_db)) -> OpsDailyResponse:
    """Daily/weekly/monthly success and error volume for the resolved filter set. Backs the ops
    dashboard's trend chart. Bucket size follows `range`, not a fixed granularity — see
    `app.services.ops_dashboard.daily_granularity`.
    """
    granularity = ops_service.daily_granularity(scope.range_)
    buckets = ops_service.resolve_daily_bounds(db, scope.run_ids_query, scope.date_from, scope.date_to, granularity)
    values = ops_service.daily_values(db, scope.run_ids_query, granularity)
    return OpsDailyResponse(
        granularity=granularity,
        points=[
            OpsDailyPoint(bucket_start=b, success_count=values.get(b, (0, 0))[0], error_count=values.get(b, (0, 0))[1])
            for b in buckets
        ],
    )


class OpsProviderRow(BaseModel):
    provider_id: int
    provider_name: str
    runs_count: int
    total_cost_usd: float | None


@router.get("/api/providers", response_model=list[OpsProviderRow])
def ops_providers(scope: OpsScope = Depends(_ops_scope), db: Session = Depends(get_db)) -> list[OpsProviderRow]:
    """Run volume and cost grouped by provider, ranked by total cost, for the resolved filter set."""
    return [OpsProviderRow(**vars(row)) for row in ops_service.provider_cost_rows(db, scope.run_ids_query)]


class OpsClientRow(BaseModel):
    client_id: int
    client_name: str
    runs_count: int
    error_count: int
    total_cost_usd: float | None


@router.get("/api/clients", response_model=list[OpsClientRow])
def ops_clients(scope: OpsScope = Depends(_ops_scope), db: Session = Depends(get_db)) -> list[OpsClientRow]:
    """Every client with at least one in-scope run, ranked by total cost — the full list, not a
    server-paginated top N (design decision 7). The ops dashboard page shows the top 15-20 and
    filters this same array client-side when the analyst searches.
    """
    return [OpsClientRow(**vars(row)) for row in ops_service.client_ops_rows(db, scope.run_ids_query)]


class OpsUserRow(BaseModel):
    user_id: int | None = Field(..., description="None for the Scheduler and unknown-attribution pseudo-rows.")
    user_name: str | None = Field(..., description="None for the Scheduler and unknown-attribution pseudo-rows.")
    is_scheduler: bool = Field(
        ..., description="True for the pseudo-row aggregating trigger_type='scheduled' runs (design decision 10)."
    )
    is_unknown_attribution: bool = Field(
        ...,
        description="True for the pseudo-row aggregating runs that predate user-attribution tracking "
        "(trigger_type='manual' with no triggered_by_user_id) — distinct from Scheduler, never folded into it.",
    )
    runs_count: int
    error_count: int
    total_cost_usd: float | None


@router.get("/api/users", response_model=list[OpsUserRow])
def ops_users(scope: OpsScope = Depends(_ops_scope), db: Session = Depends(get_db)) -> list[OpsUserRow]:
    """Every user (plus the Scheduler and unknown-attribution pseudo-rows) with at least one
    in-scope run, ranked by total cost — same full-list shape as `/api/clients`.
    """
    return [OpsUserRow(**vars(row)) for row in ops_service.user_ops_rows(db, scope.run_ids_query)]


class OpsPromptSetRow(BaseModel):
    prompt_set_id: int
    name: str
    runs_count: int
    total_cost_usd: float | None


@router.get("/api/prompt-sets", response_model=list[OpsPromptSetRow])
def ops_prompt_sets(
    request: Request,
    client_id: int = Query(..., description="Client to list prompt sets for — required."),
    scope: OpsScope = Depends(_ops_scope),
    db: Session = Depends(get_db),
) -> list[OpsPromptSetRow]:
    """Prompt sets under one client with at least one in-scope run, ranked by total cost — the
    first level of the client/prompt-set/prompt drill-down (design decision 3).
    """
    _get_client_or_404(db, request, client_id)
    narrowed = ops_service.ops_scoped_run_ids_query(
        scope.date_from, scope.date_to, client_id, None, None, None, False
    )
    return [OpsPromptSetRow(**vars(row)) for row in ops_service.prompt_set_ops_rows(db, narrowed)]


class OpsPromptRow(BaseModel):
    prompt_id: int
    text: str
    topic: str | None
    runs_count: int
    total_cost_usd: float | None


@router.get("/api/prompts", response_model=list[OpsPromptRow])
def ops_prompts(
    request: Request,
    prompt_set_id: int = Query(..., description="Prompt set to list prompts for — required."),
    scope: OpsScope = Depends(_ops_scope),
    db: Session = Depends(get_db),
) -> list[OpsPromptRow]:
    """Prompts (lineage-aggregated across versions) under one prompt set with at least one in-scope
    run, ranked by total cost — the second level of the drill-down.
    """
    _get_prompt_set_or_404(db, request, prompt_set_id)
    narrowed = ops_service.ops_scoped_run_ids_query(
        scope.date_from, scope.date_to, None, prompt_set_id, None, None, False
    )
    return [OpsPromptRow(**vars(row)) for row in ops_service.prompt_ops_rows(db, prompt_set_id, narrowed)]


class OpsModelComparisonRow(BaseModel):
    model_id: int
    model_name: str
    provider_name: str
    runs_count: int
    success_rate_pct: float | None
    avg_latency_ms: float | None
    total_cost_usd: float | None


class OpsRecentRun(BaseModel):
    run_id: int
    started_at: datetime
    model_name: str
    status: str
    latency_ms: int | None
    cost_usd: float | None
    error_message: str | None
    triggered_by_user_id: int | None
    triggered_by_user_name: str | None
    is_scheduler: bool = Field(..., description="True when trigger_type='scheduled' (design decision 10).")
    is_unknown_attribution: bool = Field(
        ..., description="True for runs that predate user-attribution tracking — distinct from Scheduler."
    )


class OpsPromptDetailResponse(BaseModel):
    """Model comparison + last 15 runs for one prompt — the innermost drill-down level."""

    prompt_id: int
    prompt_text: str
    runs_url: str = Field(..., description="Link to the existing full run-list page for this prompt.")
    models: list[OpsModelComparisonRow]
    recent_runs: list[OpsRecentRun]


@router.get("/api/prompt-detail", response_model=OpsPromptDetailResponse)
def ops_prompt_detail(
    request: Request,
    prompt_id: int = Query(..., description="Prompt to show detail for — required."),
    scope: OpsScope = Depends(_ops_scope),
    db: Session = Depends(get_db),
) -> OpsPromptDetailResponse:
    """Per-model performance comparison and the 15 most recent runs for one prompt (design decision
    6 — no new pagination is built here; "view all" links to the existing `/prompts/{id}` page,
    which already lists every run against this prompt).

    Aggregates across the prompt's whole version lineage (`root_prompt_id`) via `scope.run_ids_query`
    itself — `ops_scoped_run_ids_query` resolves `prompt_id` to its lineage internally, so this
    endpoint's numbers always agree with `/api/summary?prompt_id=X`'s (a prior version of this
    endpoint built its own separate lineage-scoped query here, which silently disagreed with
    `/api/summary`'s exact-prompt-id-only scoping — found by exercising the T3 UI against real data).
    """
    prompt = _get_prompt_or_404(db, request, prompt_id)
    models = ops_service.prompt_model_comparison_rows(db, scope.run_ids_query)
    runs = ops_service.recent_runs(db, scope.run_ids_query, with_client_name=False)
    return OpsPromptDetailResponse(
        prompt_id=prompt.id,
        prompt_text=prompt.text,
        runs_url=f"/prompts/{prompt.id}",
        models=[OpsModelComparisonRow(**vars(row)) for row in models],
        recent_runs=[
            OpsRecentRun(
                run_id=r.run_id,
                started_at=r.started_at,
                model_name=r.model_name,
                status=r.status,
                latency_ms=r.latency_ms,
                cost_usd=r.cost_usd,
                error_message=r.error_message,
                triggered_by_user_id=r.triggered_by_user_id,
                triggered_by_user_name=r.triggered_by_user_name,
                is_scheduler=r.is_scheduler,
                is_unknown_attribution=r.is_unknown_attribution,
            )
            for r in runs
        ],
    )


class OpsUserDetailByClientRow(BaseModel):
    client_id: int
    client_name: str
    runs_count: int
    total_cost_usd: float | None


class OpsUserRecentRun(BaseModel):
    run_id: int
    run_url: str
    client_name: str | None
    started_at: datetime
    model_name: str
    status: str
    latency_ms: int | None
    cost_usd: float | None


class OpsUserDetailResponse(BaseModel):
    """Breakdown by client + last 15 runs for one user (or the Scheduler pseudo-user) — the
    cross-client user axis (design decision 3).
    """

    user_id: int | None
    user_name: str | None
    is_scheduler: bool
    by_client: list[OpsUserDetailByClientRow]
    recent_runs: list[OpsUserRecentRun]


@router.get("/api/user-detail", response_model=OpsUserDetailResponse)
def ops_user_detail(
    request: Request,
    user_id: str = Query(..., description='User to show detail for: a numeric user id, or "scheduler" — required.'),
    scope: OpsScope = Depends(_ops_scope),
    db: Session = Depends(get_db),
) -> OpsUserDetailResponse:
    """Per-client breakdown and the 15 most recent runs for one user, or the Scheduler pseudo-user
    (design decision 10). No `runs_url` — there is no existing per-user run-list page to link to
    (design decision 6 rules out building new pagination inside the ops dashboard) — each recent
    run instead links to its own existing `/runs/{id}` detail page.
    """
    resolved_user_id, is_scheduler = _resolve_user_filter(request, user_id)
    user_name = None
    if not is_scheduler:
        if resolved_user_id is None:
            raise AppError("invalid_user_id", get_t(request)("errors.invalid_user_id"), status_code=400)
        user = _get_user_or_404(db, request, resolved_user_id)
        user_name = user.name

    narrowed = ops_service.ops_scoped_run_ids_query(
        scope.date_from, scope.date_to, None, None, None, resolved_user_id, is_scheduler
    )
    by_client = ops_service.client_ops_rows(db, narrowed)
    runs = ops_service.recent_runs(db, narrowed, with_client_name=True)

    return OpsUserDetailResponse(
        user_id=resolved_user_id,
        user_name=user_name,
        is_scheduler=is_scheduler,
        by_client=[
            OpsUserDetailByClientRow(client_id=row.client_id, client_name=row.client_name, runs_count=row.runs_count, total_cost_usd=row.total_cost_usd)
            for row in by_client
        ],
        recent_runs=[
            OpsUserRecentRun(
                run_id=r.run_id,
                run_url=f"/runs/{r.run_id}",
                client_name=r.client_name,
                started_at=r.started_at,
                model_name=r.model_name,
                status=r.status,
                latency_ms=r.latency_ms,
                cost_usd=r.cost_usd,
            )
            for r in runs
        ],
    )
