"""Ops dashboard aggregation queries (docs/TASKS_OPS_DASHBOARD.md T2) — the query-building layer
behind app/routers/ops_dashboard.py's JSON endpoints, mirroring the service/router split
app/services/dashboard.py already established.

Every aggregation here scopes over ALL runs (any status except 'pending' — a run mid-flight is
neither a success nor an error yet, and nothing in the agreed mockup shows a "pending" KPI), unlike
app/services/dashboard.py's client-facing queries, which only ever look at status='success' runs —
this is an internal engineering/ops view, so error visibility is the point (design decision 1).

Aggregation is always SQL (GROUP BY/SUM/COUNT/AVG), never a Python loop over full `Run` rows
(design decision 4) — including cost, via `app.services.cost.run_cost_sql_expr`.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import AIModel, Client, Prompt, PromptSet, Provider, RawResponse, Run, User
from app.services.cost import run_cost_sql_expr

DailyGranularity = Literal["day", "week", "month"]


def _cost_expr():
    """The one place `run_cost_sql_expr` is wired to this app's actual columns — every aggregation
    function below calls this instead of repeating the same four-column argument list.
    """
    return run_cost_sql_expr(
        RawResponse.token_usage, AIModel.cost_per_1k_input_usd, AIModel.cost_per_1k_output_usd, AIModel.is_free
    )


def ops_scoped_run_ids_query(
    date_from: datetime | None,
    date_to: datetime | None,
    client_id: int | None,
    prompt_set_id: int | None,
    prompt_id: int | None,
    user_id: int | None,
    is_scheduler: bool,
) -> Select:
    """Select of in-scope `Run.id` values — every ops endpoint builds on this rather than repeating
    the date/client/prompt-set/prompt/user scoping independently, the same role
    `app.services.dashboard.scoped_run_ids_query` plays for the client-facing dashboard.

    `user_id` and `is_scheduler` are mutually exclusive from the caller's perspective (the router
    resolves the `user_id` query param into exactly one of "a specific user", "the scheduler
    pseudo-user", or neither) — `is_scheduler=True` takes precedence here if both were somehow
    passed, matching design decision 10's "trigger_type='scheduled' + triggered_by_user_id IS NULL"
    pairing exactly.
    """
    query = (
        select(Run.id)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .where(Run.status != "pending")
    )
    if date_from is not None:
        query = query.where(Run.started_at >= date_from)
    if date_to is not None:
        query = query.where(Run.started_at <= date_to)
    if client_id is not None:
        query = query.where(PromptSet.client_id == client_id)
    if prompt_set_id is not None:
        query = query.where(Prompt.prompt_set_id == prompt_set_id)
    if prompt_id is not None:
        query = query.where(Run.prompt_id == prompt_id)
    if is_scheduler:
        query = query.where(Run.trigger_type == "scheduled", Run.triggered_by_user_id.is_(None))
    elif user_id is not None:
        query = query.where(Run.triggered_by_user_id == user_id)
    return query


@dataclass
class OpsSummary:
    """KPI totals for the resolved scope — backs `/ops/api/summary`."""

    runs_count: int
    success_count: int
    error_count: int
    success_rate_pct: float | None
    total_cost_usd: float | None
    avg_latency_ms: float | None


def ops_summary(db: Session, run_ids_query: Select) -> OpsSummary:
    """KPI totals for `run_ids_query`. `success_rate_pct`/`total_cost_usd`/`avg_latency_ms` are
    `None` — never a silent 0 — when there's no denominator: zero runs in scope, zero runs with a
    computable cost, or zero finished runs (an excluded 'pending' run never reaches here in the
    first place, per `ops_scoped_run_ids_query`, but the guard costs nothing and matches this
    module's "never assume a count is nonzero" discipline elsewhere).
    """
    success_count, error_count = db.execute(
        select(
            func.count(Run.id).filter(Run.status == "success"),
            func.count(Run.id).filter(Run.status == "error"),
        ).where(Run.id.in_(run_ids_query))
    ).one()
    runs_count = success_count + error_count

    total_cost = db.scalar(
        select(func.sum(_cost_expr()))
        .select_from(Run)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
    )
    avg_latency = db.scalar(
        select(func.avg(Run.latency_ms)).where(Run.id.in_(run_ids_query), Run.latency_ms.is_not(None))
    )

    return OpsSummary(
        runs_count=runs_count,
        success_count=success_count,
        error_count=error_count,
        success_rate_pct=round(100 * success_count / runs_count, 1) if runs_count else None,
        total_cost_usd=float(total_cost) if total_cost is not None else None,
        avg_latency_ms=round(float(avg_latency), 1) if avg_latency is not None else None,
    )


def daily_granularity(range_: str) -> DailyGranularity:
    """Time-bucket size for `/ops/api/daily`, per design decision 5: day for 7d/30d, week for
    90d/quarter (both roughly 90 days, same granularity), month for "all".
    """
    if range_ in ("7d", "30d"):
        return "day"
    if range_ in ("90d", "quarter"):
        return "week"
    return "month"


def _day_starts(date_from: date, date_to: date) -> list[date]:
    days, current = [], date_from
    while current <= date_to:
        days.append(current)
        current += timedelta(days=1)
    return days


def _week_starts(date_from: date, date_to: date) -> list[date]:
    start = date_from - timedelta(days=date_from.weekday())
    end = date_to - timedelta(days=date_to.weekday())
    weeks, current = [], start
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def _month_starts(date_from: date, date_to: date) -> list[date]:
    months, current = [], date_from.replace(day=1)
    end = date_to.replace(day=1)
    while current <= end:
        months.append(current)
        current = current.replace(year=current.year + 1, month=1) if current.month == 12 else current.replace(
            month=current.month + 1
        )
    return months


def resolve_daily_bounds(
    db: Session, run_ids_query: Select, date_from: datetime | None, date_to: datetime | None, granularity: DailyGranularity
) -> list[date]:
    """The bucket-start x-axis for `/ops/api/daily`: the requested bounds when given, falling back
    to the actual min/max `Run.started_at` in scope for whichever side is open-ended (range="all"),
    same fallback `app.services.dashboard.resolve_week_range` uses. Empty when there's no data and
    no bound to fall back on.
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

    from_date, to_date = effective_from.date(), effective_to.date()
    if granularity == "day":
        return _day_starts(from_date, to_date)
    if granularity == "week":
        return _week_starts(from_date, to_date)
    return _month_starts(from_date, to_date)


def daily_values(db: Session, run_ids_query: Select, granularity: DailyGranularity) -> dict[date, tuple[int, int]]:
    """{bucket_start: (success_count, error_count)} for every bucket that has at least one run —
    the caller fills every other bucket in range with (0, 0) (see `resolve_daily_bounds`).
    """
    bucket_col = func.date_trunc(granularity, func.timezone("UTC", Run.started_at)).label("bucket")
    rows = db.execute(
        select(
            bucket_col,
            func.count(Run.id).filter(Run.status == "success"),
            func.count(Run.id).filter(Run.status == "error"),
        )
        .where(Run.id.in_(run_ids_query))
        .group_by("bucket")
    ).all()
    return {row[0].date(): (row[1], row[2]) for row in rows}


@dataclass
class ProviderCostRow:
    """One row of the provider cost table — backs `/ops/api/providers`."""

    provider_id: int
    provider_name: str
    runs_count: int
    total_cost_usd: float | None


def provider_cost_rows(db: Session, run_ids_query: Select) -> list[ProviderCostRow]:
    """Cost and volume by provider across `run_ids_query`, ranked by total cost."""
    cost_expr = _cost_expr()
    rows = db.execute(
        select(Provider.id, Provider.name, func.count(Run.id), func.sum(cost_expr))
        .select_from(Run)
        .join(AIModel, Run.model_id == AIModel.id)
        .join(Provider, AIModel.provider_id == Provider.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
        .group_by(Provider.id, Provider.name)
        .order_by(func.sum(cost_expr).desc().nulls_last(), Provider.name.asc())
    ).all()
    return [
        ProviderCostRow(
            provider_id=row[0], provider_name=row[1], runs_count=row[2], total_cost_usd=float(row[3]) if row[3] is not None else None
        )
        for row in rows
    ]


@dataclass
class ClientOpsRow:
    """One row of the global clients table — backs `/ops/api/clients`. The full scoped list, not
    just a top-N page (design decision 7) — T3's Vue island truncates to the top 15-20 visually and
    filters this same array client-side when the analyst searches, rather than round-tripping to
    the server per keystroke over what stays a moderate-sized table (client/user count tracks
    company size, not run volume — design decision 7's own reasoning for skipping pagination here).
    """

    client_id: int
    client_name: str
    runs_count: int
    error_count: int
    total_cost_usd: float | None


def client_ops_rows(db: Session, run_ids_query: Select) -> list[ClientOpsRow]:
    """Cost, volume, and error counts by client across `run_ids_query`, ranked by total cost."""
    cost_expr = _cost_expr()
    rows = db.execute(
        select(
            Client.id,
            Client.name,
            func.count(Run.id),
            func.count(Run.id).filter(Run.status == "error"),
            func.sum(cost_expr),
        )
        .select_from(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .join(Client, PromptSet.client_id == Client.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
        .group_by(Client.id, Client.name)
        .order_by(func.sum(cost_expr).desc().nulls_last(), Client.name.asc())
    ).all()
    return [
        ClientOpsRow(
            client_id=row[0],
            client_name=row[1],
            runs_count=row[2],
            error_count=row[3],
            total_cost_usd=float(row[4]) if row[4] is not None else None,
        )
        for row in rows
    ]


@dataclass
class UserOpsRow:
    """One row of the global users table — backs `/ops/api/users`.

    `is_scheduler` (design decision 10) and `is_unknown_attribution` are mutually exclusive with a
    real `user_id`/`user_name`. The latter isn't in the original design decision — found by
    inspecting the real data while implementing this: rows with `trigger_type='manual'` AND
    `triggered_by_user_id IS NULL` also exist (runs that predate phase 6's user-attribution
    tracking), and are NOT scheduler runs. Grouping purely on "is the user id NULL" would silently
    fold those into the Scheduler row, overstating scheduled activity with pre-existing human work.
    They get their own bucket instead, so no run's cost/volume ever silently disappears or gets
    misattributed — the same "never mask missing data" discipline as everywhere else in this app.
    """

    user_id: int | None
    user_name: str | None
    is_scheduler: bool
    is_unknown_attribution: bool
    runs_count: int
    error_count: int
    total_cost_usd: float | None


def user_ops_rows(db: Session, run_ids_query: Select) -> list[UserOpsRow]:
    """Cost, volume, and error counts by user across `run_ids_query`, ranked by total cost — the
    same full-list-not-top-N shape as `client_ops_rows` (design decision 7).
    """
    cost_expr = _cost_expr()
    rows = db.execute(
        select(
            Run.triggered_by_user_id,
            Run.trigger_type,
            User.name,
            func.count(Run.id),
            func.count(Run.id).filter(Run.status == "error"),
            func.sum(cost_expr),
        )
        .select_from(Run)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .outerjoin(User, Run.triggered_by_user_id == User.id)
        .where(Run.id.in_(run_ids_query))
        .group_by(Run.triggered_by_user_id, Run.trigger_type, User.name)
        .order_by(func.sum(cost_expr).desc().nulls_last())
    ).all()

    result = []
    for user_id, trigger_type, name, runs_count, error_count, total_cost in rows:
        is_scheduler = user_id is None and trigger_type == "scheduled"
        is_unknown_attribution = user_id is None and trigger_type != "scheduled"
        result.append(
            UserOpsRow(
                user_id=user_id,
                user_name=name,
                is_scheduler=is_scheduler,
                is_unknown_attribution=is_unknown_attribution,
                runs_count=runs_count,
                error_count=error_count,
                total_cost_usd=float(total_cost) if total_cost is not None else None,
            )
        )
    return result


@dataclass
class PromptSetOpsRow:
    """One row of a client's prompt-set table — backs `/ops/api/prompt-sets`."""

    prompt_set_id: int
    name: str
    runs_count: int
    total_cost_usd: float | None


def prompt_set_ops_rows(db: Session, run_ids_query: Select) -> list[PromptSetOpsRow]:
    """Cost and volume by prompt set across `run_ids_query` (already narrowed to one client by the
    caller). Only prompt sets with at least one in-scope run appear — same "show what actually
    happened" precedent as `app.services.dashboard`'s league tables, not every prompt set that
    merely exists under the client regardless of activity.
    """
    cost_expr = _cost_expr()
    rows = db.execute(
        select(PromptSet.id, PromptSet.name, func.count(Run.id), func.sum(cost_expr))
        .select_from(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
        .group_by(PromptSet.id, PromptSet.name)
        .order_by(func.sum(cost_expr).desc().nulls_last(), PromptSet.name.asc())
    ).all()
    return [
        PromptSetOpsRow(prompt_set_id=row[0], name=row[1], runs_count=row[2], total_cost_usd=float(row[3]) if row[3] is not None else None)
        for row in rows
    ]


@dataclass
class PromptOpsRow:
    """One row of a prompt-set's prompts table — backs `/ops/api/prompts`. `runs_count`/
    `total_cost_usd` roll up every version in the prompt's lineage (`root_prompt_id`), not just the
    current version — the same lineage walk `app/services/export.py` already uses so a run's
    history doesn't vanish from view just because the prompt text was later edited (a code-review
    precedent for exactly this rollup, not a fresh invention here).
    """

    prompt_id: int
    text: str
    topic: str | None
    runs_count: int
    total_cost_usd: float | None


def prompt_ops_rows(db: Session, db_prompt_set_id: int, run_ids_query: Select) -> list[PromptOpsRow]:
    """Cost and volume by prompt (lineage-aggregated) for one prompt set, scoped by `run_ids_query`.

    Only the current version of each lineage is listed as a row (`is_current_version`), but that
    row's counts sum every version's in-scope runs — `Prompt.root_prompt_id` links every version of
    the same logical prompt (see `app.models.prompt.Prompt`'s docstring), so
    `COALESCE(root_prompt_id, id)` groups a lineage together regardless of which version's `id` a
    given historical `Run.prompt_id` actually points at. Same "only entities with in-scope activity
    appear" behavior as `prompt_set_ops_rows` — a lineage with zero in-scope runs is omitted, not
    shown as a zero row.
    """
    lineage_key = func.coalesce(Prompt.root_prompt_id, Prompt.id)
    cost_expr = _cost_expr()

    rows = db.execute(
        select(lineage_key, func.count(Run.id), func.sum(cost_expr))
        .select_from(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query), Prompt.prompt_set_id == db_prompt_set_id)
        .group_by(lineage_key)
    ).all()
    totals_by_root = {row[0]: (row[1], row[2]) for row in rows}
    if not totals_by_root:
        return []

    current_versions = db.scalars(
        select(Prompt).where(
            Prompt.prompt_set_id == db_prompt_set_id,
            Prompt.is_current_version.is_(True),
            lineage_key.in_(totals_by_root.keys()),
        )
    ).all()

    result = []
    for current in current_versions:
        runs_count, total_cost = totals_by_root[current.root_prompt_id or current.id]
        result.append(
            PromptOpsRow(
                prompt_id=current.id,
                text=current.text,
                topic=current.topic,
                runs_count=runs_count,
                total_cost_usd=float(total_cost) if total_cost is not None else None,
            )
        )
    result.sort(key=lambda r: (r.total_cost_usd is None, -(r.total_cost_usd or 0)))
    return result


@dataclass
class ModelComparisonRow:
    """One row of a prompt's model-comparison table — backs `/ops/api/prompt-detail`."""

    model_id: int
    model_name: str
    provider_name: str
    runs_count: int
    success_rate_pct: float | None
    avg_latency_ms: float | None
    total_cost_usd: float | None


def prompt_model_comparison_rows(db: Session, run_ids_query: Select) -> list[ModelComparisonRow]:
    """Per-model breakdown across `run_ids_query` (already narrowed to one prompt's full lineage by
    the caller) — how each model that has ever answered this prompt performed against it.
    """
    cost_expr = _cost_expr()
    rows = db.execute(
        select(
            AIModel.id,
            AIModel.model_name,
            Provider.name,
            func.count(Run.id),
            func.count(Run.id).filter(Run.status == "success"),
            func.avg(Run.latency_ms).filter(Run.latency_ms.is_not(None)),
            func.sum(cost_expr),
        )
        .select_from(Run)
        .join(AIModel, Run.model_id == AIModel.id)
        .join(Provider, AIModel.provider_id == Provider.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
        .group_by(AIModel.id, AIModel.model_name, Provider.name)
        .order_by(func.count(Run.id).desc(), AIModel.model_name.asc())
    ).all()
    return [
        ModelComparisonRow(
            model_id=row[0],
            model_name=row[1],
            provider_name=row[2],
            runs_count=row[3],
            success_rate_pct=round(100 * row[4] / row[3], 1) if row[3] else None,
            avg_latency_ms=round(float(row[5]), 1) if row[5] is not None else None,
            total_cost_usd=float(row[6]) if row[6] is not None else None,
        )
        for row in rows
    ]


@dataclass
class RecentRunRow:
    """One row of a "last ~15 runs" list — backs both `/ops/api/prompt-detail` and
    `/ops/api/user-detail` (design decision 6). Cost here is computed via the Python
    `estimate_run_cost`, not the SQL expression — this is always a bounded ~15-row fetch, not a
    full-table aggregation, so it isn't the pattern design decision 4 rules out.
    """

    run_id: int
    started_at: datetime
    model_name: str
    status: str
    latency_ms: int | None
    cost_usd: float | None
    client_name: str | None = None


def recent_runs(db: Session, run_ids_query: Select, *, with_client_name: bool, limit: int = 15) -> list[RecentRunRow]:
    """The most recent `limit` runs in `run_ids_query`, most recent first."""
    from app.services.cost import estimate_run_cost  # local import: avoids a cost.py <-> here cycle at module load

    query = (
        select(Run, AIModel, RawResponse)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
        .order_by(Run.started_at.desc())
        .limit(limit)
    )
    if with_client_name:
        query = (
            query.join(Prompt, Run.prompt_id == Prompt.id)
            .join(PromptSet, Prompt.prompt_set_id == PromptSet.id)
            .join(Client, PromptSet.client_id == Client.id)
            .add_columns(Client.name.label("client_name"))
        )

    rows = db.execute(query).all()
    return [
        RecentRunRow(
            run_id=row.Run.id,
            started_at=row.Run.started_at,
            model_name=row.AIModel.model_name,
            status=row.Run.status,
            latency_ms=row.Run.latency_ms,
            cost_usd=estimate_run_cost(row.RawResponse.token_usage if row.RawResponse else None, row.AIModel),
            client_name=row.client_name if with_client_name else None,
        )
        for row in rows
    ]
