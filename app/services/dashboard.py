"""Dashboard v0 aggregation queries (docs/TASKS_PHASE4.md) — the query-building layer behind
app/routers/dashboard.py's JSON endpoints, split out so it can be reused and unit-tested
independently of the HTTP layer, the same service/router split app/services/export.py already
established for runs export.

Every function here is read-only: aggregates existing citations/runs/raw_responses/
analysis_results, writes nothing. `own_domain_rate`/`weekly_values`'s own_rate metric are the one
figures that reuse another layer's output (the `mention_visibility` result from phase 3) rather
than recomputing domain matching themselves — see design decision 6.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import AIModel, AnalysisResult, AnalysisSkill, Citation, Client, Prompt, PromptSet, RawResponse, Run
from app.utils import is_own_domain

DashboardRange = Literal["30d", "90d", "quarter", "all"]
DashboardMetric = Literal["citations", "runs", "own_rate"]


def range_bounds(range_: DashboardRange) -> tuple[datetime | None, datetime | None]:
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


def scoped_run_ids_query(
    client_id: int,
    date_from: datetime | None,
    date_to: datetime | None,
    market_id: int | None,
    provider_id: int | None,
) -> Select:
    """Select of successful `Run.id` values belonging to one client, narrowed by the shared filter
    set — every dashboard query builds on this rather than repeating the client/date/market/
    provider scoping independently. Deliberately takes no `db` — building a `Select` needs no
    session, only `db.execute()`/`db.scalar()` at the call site does.
    """
    query = (
        select(Run.id)
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


def week_starts(date_from: datetime, date_to: datetime) -> list[date]:
    """Every Monday-aligned week start between `date_from` and `date_to`, inclusive of both ends'
    weeks — the full x-axis a time series chart needs, independent of which weeks actually have data.
    """
    start = date_from.date() - timedelta(days=date_from.weekday())
    end = date_to.date() - timedelta(days=date_to.weekday())
    weeks = []
    current = start
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def resolve_week_range(
    db: Session, run_ids_query: Select, date_from: datetime | None, date_to: datetime | None
) -> list[date]:
    """The week-start x-axis for the timeseries endpoint: the requested bounds when given, falling
    back to the actual min/max `Run.started_at` in scope for whichever side is open-ended
    (`range=all`). Empty when there's no data and no bound to fall back on — nothing to plot.
    """
    effective_from, effective_to = date_from, date_to
    if effective_from is None or effective_to is None:
        bounds = db.execute(
            select(func.min(Run.started_at), func.max(Run.started_at)).where(Run.id.in_(run_ids_query))
        ).one()
        if bounds[0] is None:
            return []
        effective_from = effective_from or bounds[0]
        effective_to = effective_to or bounds[1]
    return week_starts(effective_from, effective_to)


def count_runs(db: Session, run_ids_query: Select) -> int:
    """Row count of a run_ids_query — shared by every place that needs "how many runs in scope"."""
    return db.scalar(select(func.count()).select_from(run_ids_query.subquery())) or 0


def citation_totals(db: Session, run_ids_query: Select) -> tuple[int, int]:
    """(citations_count, distinct_domains_count) across the citations of runs in `run_ids_query`,
    in one round trip — count(DISTINCT source_domain) already excludes NULL source_domain values
    per SQL semantics, so no extra is_not(None) filter is needed either.
    """
    citations_count, distinct_domains_count = db.execute(
        select(func.count(Citation.id), func.count(func.distinct(Citation.source_domain)))
        .join(RawResponse, Citation.raw_response_id == RawResponse.id)
        .where(RawResponse.run_id.in_(run_ids_query))
    ).one()
    return citations_count or 0, distinct_domains_count or 0


def _mention_visibility_base_query(run_ids_query: Select) -> Select:
    """AnalysisResult rows for the mention_visibility skill, scoped to `run_ids_query` — the
    join+filter shared by `own_domain_rate` and `weekly_values`'s own_rate metric, so the two can
    never independently drift on what "own-domain cited" means for the same run set.
    """
    return (
        select(AnalysisResult)
        .join(RawResponse, AnalysisResult.raw_response_id == RawResponse.id)
        .join(AnalysisSkill, AnalysisResult.analysis_skill_id == AnalysisSkill.id)
        .where(AnalysisSkill.key == "mention_visibility", RawResponse.run_id.in_(run_ids_query))
    )


def own_domain_rate(db: Session, client: Client, run_ids_query: Select) -> float | None:
    """Share (0-100) of runs in `run_ids_query` whose mention_visibility result has cited=true.

    None when the client has no domain set (citation-matching never ran for them, per phase 3
    design decision 3) or when no run in scope has a mention_visibility result yet — a rate needs
    both a numerator and a meaningful denominator, not an arbitrary 0.
    """
    if not client.domain:
        return None

    base = _mention_visibility_base_query(run_ids_query)
    total, cited = db.execute(
        base.with_only_columns(
            func.count(AnalysisResult.id),
            func.count(AnalysisResult.id).filter(AnalysisResult.output["cited"].astext == "true"),
        )
    ).one()
    if not total:
        return None
    return round(100 * cited / total, 1)


def weekly_values(db: Session, run_ids_query: Select, metric: DashboardMetric) -> dict[date, float]:
    """{week_start: value} for the requested metric — only weeks with at least one row, the caller
    fills every other week in range with 0 (see `week_starts`/`resolve_week_range`).
    """
    # date_trunc('week', ...) on a timestamptz buckets in the session's TimeZone GUC, not
    # necessarily UTC — week_starts (the x-axis this dict is looked up against) always computes
    # boundaries in UTC. Pin the truncation to UTC explicitly rather than relying on the session
    # default staying UTC, so the two never silently drift apart.
    week_col = func.date_trunc("week", func.timezone("UTC", Run.started_at)).label("week")

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
    base = _mention_visibility_base_query(run_ids_query).join(Run, RawResponse.run_id == Run.id)
    rows = db.execute(
        base.with_only_columns(
            week_col,
            func.count(AnalysisResult.id),
            func.count(AnalysisResult.id).filter(AnalysisResult.output["cited"].astext == "true"),
        ).group_by("week")
    ).all()
    return {row[0].date(): (round(100 * row[2] / row[1], 1) if row[1] else 0.0) for row in rows}


@dataclass
class DomainLeagueRow:
    """One row of the cited-domains league table — plain data, wrapped into the router's
    Pydantic DomainRow response model.
    """

    rank: int
    domain: str
    citations_count: int
    run_coverage_pct: float
    first_seen: date
    last_seen: date
    is_own_domain: bool


def domain_league_rows(db: Session, client: Client, run_ids_query: Select, limit: int) -> list[DomainLeagueRow]:
    """League table of domains cited across `run_ids_query`, ranked by citation count."""
    total_runs = count_runs(db, run_ids_query)

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
        # Secondary sort key so domains tied on citations_count get a stable, deterministic
        # order/rank across requests — without it, which tied domain lands inside `limit` (and
        # what rank it gets) could flip between identical requests with unchanged data.
        .order_by(func.count(Citation.id).desc(), Citation.source_domain.asc())
        .limit(limit)
    ).all()

    return [
        DomainLeagueRow(
            rank=rank,
            domain=row.source_domain,
            citations_count=row.citations_count,
            run_coverage_pct=round(100 * row.run_count / total_runs, 1) if total_runs else 0.0,
            first_seen=row.first_seen.date(),
            last_seen=row.last_seen.date(),
            is_own_domain=is_own_domain(row.source_domain, client.domain),
        )
        for rank, row in enumerate(rows, start=1)
    ]
