"""RunSchedule and RunQueueItem — the recurrence rule and the concrete occurrence it produces.

Three separate layers, deliberately not folded into `Run` (docs/TASKS_SCHEDULER.md design
decision 1): `RunSchedule` is a recurrence rule ("every Mon and Thu at 6:00"), `RunQueueItem` is
one concrete occurrence of that rule ("Monday 6:00") and doubles as its own history, and
`Run`/`RawResponse`/`Citation` remain the untouched evidence trail. Same split as Airflow's
DAG -> DagRun -> TaskInstance or GitHub Actions' workflow -> workflow_run -> job. `Run`'s
documented two-phase lifecycle (app/models/run.py) is unaffected: a `Run` row still only comes
into existence when a prompt is actually executed, scheduled or not.

This module only defines schema (docs/TASKS_SCHEDULER.md T0) — the ticker, executor, lease and
reconciliation logic live in app/services/queue.py and app/worker.py, added in a later task.
"""

from datetime import date, datetime, time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.market import Market
    from app.models.persona import Persona
    from app.models.prompt import Prompt
    from app.models.provider import AIModel
    from app.models.run import Run
    from app.models.user import User


class RunSchedule(Base):
    """A recurrence rule: what to run, on which models/personas, how often, and when it ends.

    `target_id` points at the lineage of a `Prompt` (its `root_prompt_id`, or its own id when it
    has no later version) when `target_type == 'prompt'`, or at a `PromptSet.id` when
    `target_type == 'prompt_set'` — a plain integer rather than a foreign key, because which table
    it names depends on `target_type` (docs/TASKS_SCHEDULER.md design decision 6). Resolving that
    lineage to the current, concrete `Prompt` version (or the prompt set's current active prompts)
    happens at enqueue time in app/services/queue.py, not here — so an edited prompt is picked up
    on the very next window instead of the schedule silently running stale text forever.

    `model_ids` and `persona_ids` are arrays because one schedule can target several models and
    several personas at once (design decision 34) — comparing across models/personas is the whole
    point, and prompts x models x personas all multiply into separate `RunQueueItem` rows at
    enqueue time.

    The recurrence rule itself is stored as a local wall-clock time plus an IANA zone, never a
    precomputed UTC instant (design decision 7) — `time_of_day`/`days_of_week`/`day_of_month`/
    `timezone` are the rule; `next_run_at` is only a derived, indexed cache for
    `WHERE next_run_at <= now()` and must always be recomputed from the occurrence's own due time,
    never from `now()` (design decision 9), or the schedule would drift a few minutes every run.

    Every schedule has a mandatory end — exactly one of `ends_on`/`max_occurrences` is set,
    enforced by the CHECK constraint below (design decision 31) so a forgotten schedule cannot
    spend money forever unnoticed. `occurrences_count` tracks progress against `max_occurrences`.

    `is_active = False` covers three distinct reasons recorded in `inactive_reason`: the owning
    user toggled it off (`'user'`), its owner was deactivated (`'owner_deactivated'`, design
    decision 23), or it ran its course (`'completed'`, design decision 31) — a schedule is never
    deleted just for reaching its end, so it stays visible with its history.
    """

    __tablename__ = "run_schedules"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(ends_on, max_occurrences) = 1",
            name="ck_run_schedules_exactly_one_end",
        ),
        Index("idx_run_schedules_next_run_at", "next_run_at", postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"))
    persona_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    days_of_week: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    time_of_day: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Prague")
    starts_on: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    ends_on: Mapped[date | None] = mapped_column(Date)
    max_occurrences: Mapped[int | None] = mapped_column(Integer)
    occurrences_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    inactive_reason: Mapped[str | None] = mapped_column(String(30))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped["Client"] = relationship()
    market: Mapped["Market | None"] = relationship()
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped["User | None"] = relationship(foreign_keys=[updated_by_user_id])
    queue_items: Mapped[list["RunQueueItem"]] = relationship(back_populates="schedule")


class RunQueueItem(Base):
    """One concrete occurrence of a schedule's rule, and simultaneously its own permanent history
    record (docs/TASKS_SCHEDULER.md design decision 27) — rows are never deleted, only appended.

    `schedule_id` is NULL for manual/batch-triggered items (`source` distinguishes the three);
    the queue's shape already accommodates a manual run going through it, even though this branch
    does not yet route manual triggers here (design decision 5).

    `prompt_id`/`model_id`/`market_id`/`persona_id` are always the concrete, resolved values for
    this one occurrence — a schedule targeting several models/personas (or a whole prompt set,
    design decision 32) fans out into one `RunQueueItem` row per (prompt, model, persona)
    combination, all sharing one `batch_id` when they came from the same window.

    The five-column unique constraint (design decision 12, widened in v1.1 for design decision 32)
    is the enqueue-side idempotence guard: a ticker that runs twice, or crashes between inserting
    a window's rows and advancing `next_run_at`, must not be able to insert the same
    (schedule, window, prompt, model, persona) combination twice — a two-column key would let a
    225-row window's items reject each other instead of the true duplicates.

    `run_id` is written before the provider adapter is ever called (design decision 13) — the
    idempotence guard against paying twice for one occurrence lives on this column, not on a lock
    held for the adapter call's duration, because a killed worker releases locks but must not
    cause the occurrence to be picked up and paid for again.

    `retry_of_id` (T7) links a manually-retried item back to the one it retries — set only on the
    NEW row, never written onto the old one, since retrying never rewrites history (NFR-6): the
    original keeps whatever terminal status ('error'/'skipped'/'cancelled') it actually ended with.
    """

    __tablename__ = "run_queue"
    __table_args__ = (
        CheckConstraint(
            "source IN ('schedule', 'manual', 'batch')",
            name="ck_run_queue_source",
        ),
        CheckConstraint(
            "status IN ('queued', 'leased', 'done', 'error', 'skipped', 'deferred', 'cancelled')",
            name="ck_run_queue_status",
        ),
        Index(
            "idx_run_queue_unique_occurrence",
            "schedule_id",
            "scheduled_for",
            "prompt_id",
            "model_id",
            "persona_id",
            unique=True,
        ),
        Index(
            "idx_run_queue_claim_order",
            text("priority DESC"),
            "scheduled_for",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("idx_run_queue_schedule_history", "schedule_id", text("scheduled_for DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("run_schedules.id"))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    prompt_id: Mapped[int] = mapped_column(ForeignKey("prompts.id"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    persona_id: Mapped[int] = mapped_column(ForeignKey("personas.id"), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued", server_default="queued")
    skip_reason: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    leased_by: Mapped[str | None] = mapped_column(String(64))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    # T7: which item this one retries, if any — never rewritten onto the original row itself, so
    # a retry is always a new row and the item it retries stays queryable, unchanged (NFR-6).
    retry_of_id: Mapped[int | None] = mapped_column(ForeignKey("run_queue.id"))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    schedule: Mapped["RunSchedule | None"] = relationship(back_populates="queue_items")
    client: Mapped["Client"] = relationship()
    prompt: Mapped["Prompt"] = relationship()
    model: Mapped["AIModel"] = relationship()
    market: Mapped["Market"] = relationship()
    persona: Mapped["Persona"] = relationship()
    retry_of: Mapped["RunQueueItem | None"] = relationship(remote_side=[id])
    run: Mapped["Run | None"] = relationship()
