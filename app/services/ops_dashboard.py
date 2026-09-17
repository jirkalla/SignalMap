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
from datetime import date, datetime
from typing import Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import AIModel, Client, Prompt, PromptSet, Provider, RawResponse, Run, User
from app.services.cost import run_cost_sql_expr, run_token_sql_expr
from app.services.date_ranges import day_starts, month_starts, week_starts

DailyGranularity = Literal["day", "week", "month"]


def _cost_expr():
    """The one place `run_cost_sql_expr` is wired to this app's actual columns — every aggregation
    function below calls this instead of repeating the same four-column argument list.

    Prices from `ai_model_price_components`, effective at each run's own `started_at` (docs/
    TASKS_COST_COMPONENTS.md CC-4) — not `AIModel.cost_per_1k_*_usd`/today's price.
    """
    return run_cost_sql_expr(RawResponse.token_usage, Run.model_id, Run.started_at, AIModel.is_free)


def _token_expr(axis: str):
    """The one place `run_token_sql_expr` is wired to `RawResponse.token_usage` — every aggregation
    function below that needs a token total calls this instead of repeating the column reference.
    """
    return run_token_sql_expr(RawResponse.token_usage, axis)


def _classify_attribution(user_id: int | None, trigger_type: str) -> tuple[bool, bool]:
    """(is_scheduler, is_unknown_attribution) for one run's `triggered_by_user_id`/`trigger_type`
    (design decision 10, plus the unknown-attribution case found during T2) — the one place
    `user_ops_rows` and `recent_runs` both derive this three-way split from, instead of each
    re-deriving the same two boolean expressions independently (code review finding).
    """
    is_scheduler = user_id is None and trigger_type == "scheduled"
    is_unknown_attribution = user_id is None and trigger_type != "scheduled"
    return is_scheduler, is_unknown_attribution


def ops_scoped_run_ids_query(
    date_from: datetime | None,
    date_to: datetime | None,
    *,
    client_id: int | None = None,
    prompt_set_id: int | None = None,
    prompt_id: int | None = None,
    user_id: int | None = None,
    is_scheduler: bool = False,
) -> Select:
    """Select of in-scope `Run.id` values — every ops endpoint builds on this rather than repeating
    the date/client/prompt-set/prompt/user scoping independently, the same role
    `app.services.dashboard.scoped_run_ids_query` plays for the client-facing dashboard.

    The filter params are keyword-only (code review finding): they're all `int | None`, and every
    narrowing call site only sets one or two of them — positional calls previously had to pass
    `None`/`False` for the rest, where two same-typed args could be transposed with no type error.

    `prompt_id` scopes to that prompt's *whole version lineage* (`root_prompt_id`), not just the
    exact row id — the same rollup `prompt_ops_rows`/`prompt_model_comparison_rows` already use, so
    `/api/summary?prompt_id=X` and `/api/prompt-detail?prompt_id=X` always agree on how many runs
    are "this prompt's runs" instead of one counting only the current version and the other
    counting the full history (found by exercising T3's prompt-detail view against real data,
    where the two disagreed before this fix).

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
        root_id_subq = select(func.coalesce(Prompt.root_prompt_id, Prompt.id)).where(Prompt.id == prompt_id).scalar_subquery()
        lineage_ids_subq = select(Prompt.id).where(or_(Prompt.id == root_id_subq, Prompt.root_prompt_id == root_id_subq))
        query = query.where(Run.prompt_id.in_(lineage_ids_subq))
    if is_scheduler:
        query = query.where(Run.trigger_type == "scheduled", Run.triggered_by_user_id.is_(None))
    elif user_id is not None:
        query = query.where(Run.triggered_by_user_id == user_id)
    return query


@dataclass
class OpsSummary:
    """KPI totals for the resolved scope — backs `/ops/api/summary`.

    `total_*_tokens` (docs/TASKS_COST_COMPONENTS.md CC-5, design decision 7 — tokens shown
    alongside cost, never instead of it) are `None` only when no run in scope has a `raw_responses`
    row at all; a run that has one but reports zero of a given axis counts as 0, not `None` — see
    `app.services.cost.run_token_sql_expr`'s docstring for how that distinction is preserved
    through `SUM()`.
    """

    runs_count: int
    success_count: int
    error_count: int
    success_rate_pct: float | None
    total_cost_usd: float | None
    avg_latency_ms: float | None
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_cache_read_tokens: int | None
    total_cache_write_tokens: int | None


def ops_summary(db: Session, run_ids_query: Select) -> OpsSummary:
    """KPI totals for `run_ids_query`. `success_rate_pct`/`total_cost_usd`/`avg_latency_ms` are
    `None` — never a silent 0 — when there's no denominator: zero runs in scope, zero runs with a
    computable cost, or zero finished runs (an excluded 'pending' run never reaches here in the
    first place, per `ops_scoped_run_ids_query`, but the guard costs nothing and matches this
    module's "never assume a count is nonzero" discipline elsewhere).

    One round trip, not three (code review finding) — `AIModel` is a required FK on `Run` and
    `RawResponse` is unique per `run_id`, so joining both here doesn't change the row count per Run,
    and all four (now eight) aggregates can share the one join. `AVG()` skips SQL NULL `latency_ms`
    values on its own, same relied-on Postgres behavior `avg_share_of_voice`
    (app/services/dashboard.py) already documents — no extra `IS NOT NULL` filter needed to get a
    correct average.
    """
    (
        success_count,
        error_count,
        total_cost,
        avg_latency,
        total_input,
        total_output,
        total_cache_read,
        total_cache_write,
    ) = db.execute(
        select(
            func.count(Run.id).filter(Run.status == "success"),
            func.count(Run.id).filter(Run.status == "error"),
            func.sum(_cost_expr()),
            func.avg(Run.latency_ms),
            func.sum(_token_expr("input")),
            func.sum(_token_expr("output")),
            func.sum(_token_expr("cache_read")),
            func.sum(_token_expr("cache_write")),
        )
        .select_from(Run)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .where(Run.id.in_(run_ids_query))
    ).one()
    runs_count = success_count + error_count

    return OpsSummary(
        runs_count=runs_count,
        success_count=success_count,
        error_count=error_count,
        success_rate_pct=round(100 * success_count / runs_count, 1) if runs_count else None,
        total_cost_usd=float(total_cost) if total_cost is not None else None,
        avg_latency_ms=round(float(avg_latency), 1) if avg_latency is not None else None,
        total_input_tokens=int(total_input) if total_input is not None else None,
        total_output_tokens=int(total_output) if total_output is not None else None,
        total_cache_read_tokens=int(total_cache_read) if total_cache_read is not None else None,
        total_cache_write_tokens=int(total_cache_write) if total_cache_write is not None else None,
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


def resolve_daily_bounds(
    db: Session, run_ids_query: Select, date_from: datetime | None, date_to: datetime | None, granularity: DailyGranularity
) -> list[date]:
    """The bucket-start x-axis for `/ops/api/daily`: the requested bounds when given, falling back
    to the actual min/max `Run.started_at` in scope for whichever side is open-ended (range="all"),
    same fallback `app.services.dashboard.resolve_week_range` uses. Empty when there's no data and
    no bound to fall back on.

    `day_starts`/`week_starts`/`month_starts` (app.services.date_ranges, code review finding) are
    shared with app.services.dashboard's own week-bucketing — one definition per granularity, not
    an independently-maintained copy per dashboard.
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
        return day_starts(from_date, to_date)
    if granularity == "week":
        return week_starts(from_date, to_date)
    return month_starts(from_date, to_date)


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
    """One row of the provider cost table — backs `/ops/api/providers`. `total_input_tokens`/
    `total_output_tokens` follow the same `None`-only-when-no-raw_responses-row rule as
    `OpsSummary`'s token totals.
    """

    provider_id: int
    provider_name: str
    runs_count: int
    total_cost_usd: float | None
    total_input_tokens: int | None
    total_output_tokens: int | None


def provider_cost_rows(db: Session, run_ids_query: Select) -> list[ProviderCostRow]:
    """Cost, volume, and token totals by provider across `run_ids_query`, ranked by total cost."""
    cost_expr = _cost_expr()
    input_expr = _token_expr("input")
    output_expr = _token_expr("output")
    rows = db.execute(
        select(Provider.id, Provider.name, func.count(Run.id), func.sum(cost_expr), func.sum(input_expr), func.sum(output_expr))
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
            provider_id=row[0],
            provider_name=row[1],
            runs_count=row[2],
            total_cost_usd=float(row[3]) if row[3] is not None else None,
            total_input_tokens=int(row[4]) if row[4] is not None else None,
            total_output_tokens=int(row[5]) if row[5] is not None else None,
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
        is_scheduler, is_unknown_attribution = _classify_attribution(user_id, trigger_type)
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

    One query, not two (code review finding): a second `Prompt` alias (`current`), joined on "same
    lineage, is the current version", supplies the row's displayed `text`/`topic`/`id` directly
    alongside the aggregate, instead of a separate follow-up `SELECT` against `Prompt` to look up
    that same information for each lineage found by the first query.
    """
    lineage_key = func.coalesce(Prompt.root_prompt_id, Prompt.id)
    current = aliased(Prompt)
    current_lineage_key = func.coalesce(current.root_prompt_id, current.id)
    cost_expr = _cost_expr()

    rows = db.execute(
        select(current.id, current.text, current.topic, func.count(Run.id), func.sum(cost_expr))
        .select_from(Run)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .join(current, and_(current_lineage_key == lineage_key, current.is_current_version.is_(True)))
        .where(Run.id.in_(run_ids_query), Prompt.prompt_set_id == db_prompt_set_id)
        .group_by(current.id, current.text, current.topic)
        .order_by(func.sum(cost_expr).desc().nulls_last(), current.text.asc())
    ).all()
    return [
        PromptOpsRow(
            prompt_id=row[0], text=row[1], topic=row[2], runs_count=row[3], total_cost_usd=float(row[4]) if row[4] is not None else None
        )
        for row in rows
    ]


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

    `triggered_by_user_id`/`triggered_by_user_name`/`is_scheduler`/`is_unknown_attribution` mirror
    `UserOpsRow`'s three-way split (design decision 10 plus the unknown-attribution case found
    during T2) — a flag set, not a pre-baked label, so the Vue island localizes "Scheduler" itself
    rather than the backend shipping UI text (i18n rule).

    `input_tokens`/`output_tokens` (docs/TASKS_COST_COMPONENTS.md CC-5) are the RAW counts the
    provider reported (`app.services.cost.raw_input_output_tokens`), not netted against cache —
    same design decision 7 as `OpsSummary`'s token totals.
    """

    run_id: int
    started_at: datetime
    model_name: str
    status: str
    latency_ms: int | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    error_message: str | None
    triggered_by_user_id: int | None
    triggered_by_user_name: str | None
    is_scheduler: bool
    is_unknown_attribution: bool
    client_name: str | None = None


def recent_runs(db: Session, run_ids_query: Select, *, with_client_name: bool, limit: int = 15) -> list[RecentRunRow]:
    """The most recent `limit` runs in `run_ids_query`, most recent first."""
    from app.services.cost import (  # avoids a cost.py <-> here cycle
        estimate_run_cost,
        load_price_components,
        prices_at,
        raw_input_output_tokens,
    )

    query = (
        select(Run, AIModel, RawResponse, User)
        .join(AIModel, Run.model_id == AIModel.id)
        .outerjoin(RawResponse, RawResponse.run_id == Run.id)
        .outerjoin(User, Run.triggered_by_user_id == User.id)
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
    # One query for every model these ~15 rows reference, not one query per row (same discipline
    # as app/routers/ai_models.py's _model_rows) — each row then resolves its OWN price as of its
    # OWN started_at (docs/TASKS_COST_COMPONENTS.md design decision 4), not today's price.
    components_by_model = load_price_components(db, list({row.AIModel.id for row in rows}))

    result = []
    for row in rows:
        is_scheduler, is_unknown_attribution = _classify_attribution(row.Run.triggered_by_user_id, row.Run.trigger_type)
        prices = prices_at(components_by_model.get(row.AIModel.id, []), row.Run.started_at)
        token_usage = row.RawResponse.token_usage if row.RawResponse else None
        input_tokens, output_tokens = raw_input_output_tokens(token_usage)
        result.append(
            RecentRunRow(
                run_id=row.Run.id,
                started_at=row.Run.started_at,
                model_name=row.AIModel.model_name,
                status=row.Run.status,
                latency_ms=row.Run.latency_ms,
                cost_usd=estimate_run_cost(token_usage, row.AIModel, prices),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_message=row.Run.error_message,
                triggered_by_user_id=row.Run.triggered_by_user_id,
                triggered_by_user_name=row.User.name if row.User else None,
                is_scheduler=is_scheduler,
                is_unknown_attribution=is_unknown_attribution,
                client_name=row.client_name if with_client_name else None,
            )
        )
    return result
