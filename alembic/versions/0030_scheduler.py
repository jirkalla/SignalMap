"""Add scheduler, run queue and notification tables.

Schema-only migration for docs/TASKS_SCHEDULER.md T0 — no business logic, no data migration.
Realizes the three-layer split from design decision 1: `run_schedules` (a recurrence rule),
`run_queue` (a concrete occurrence, doubling as its own history per design decision 27), and the
existing `runs`/`raw_responses`/`citations` evidence trail, left untouched.

Originally planned as revision 0029 in the design doc, but 0029 was taken by
`clients_is_test` (merged first, as the doc's own note anticipated) — this is 0030.

Four new tables:
  - `run_schedules`   — the recurrence rule; mandatory end enforced by a CHECK constraint
                        (design decision 31: a schedule can never be saved to run forever).
  - `run_queue`        — one row per concrete occurrence; the five-column unique constraint is
                        the enqueue-side idempotence guard (design decisions 12 and 32 — a
                        two-column key would let a multi-model/persona window's own rows reject
                        each other instead of true duplicates).
  - `worker_heartbeats` — liveness, so a stopped worker doesn't look like an empty queue
                        (design decision 19).
  - `notification_outbox` — event outbox, one row per raised event regardless of which delivery
                        channels exist yet (design decision 25).

Three new columns on `clients` (priority, daily_run_limit, monthly_budget_usd) for the two
distinct caps described in design decision 33.

One existing index widened: `idx_runs_one_pending_per_prompt_model` (migration 0024) gains
persona_id and market_id (design decision 35) — see app/models/run.py's docstring for why this
is a completion of the original "same prompt+model" guard, not a weakening of it.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-20

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("model_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("persona_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("days_of_week", postgresql.ARRAY(sa.SmallInteger()), nullable=True),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=True),
        sa.Column("time_of_day", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Prague"),
        sa.Column("starts_on", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("max_occurrences", sa.Integer(), nullable=True),
        sa.Column("occurrences_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("inactive_reason", sa.String(30), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(ends_on, max_occurrences) = 1",
            name="ck_run_schedules_exactly_one_end",
        ),
    )
    op.create_index(
        "idx_run_schedules_next_run_at",
        "run_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "run_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("run_schedules.id"), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("prompt_id", sa.Integer(), sa.ForeignKey("prompts.id"), nullable=False),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("ai_models.id"), nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("persona_id", sa.Integer(), sa.ForeignKey("personas.id"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("skip_reason", sa.String(40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("leased_by", sa.String(64), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('schedule', 'manual', 'batch')", name="ck_run_queue_source"),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'done', 'error', 'skipped', 'deferred', 'cancelled')",
            name="ck_run_queue_status",
        ),
    )
    op.create_index(
        "idx_run_queue_unique_occurrence",
        "run_queue",
        ["schedule_id", "scheduled_for", "prompt_id", "model_id", "persona_id"],
        unique=True,
    )
    op.create_index(
        "idx_run_queue_claim_order",
        "run_queue",
        [sa.text("priority DESC"), "scheduled_for"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "idx_run_queue_schedule_history",
        "run_queue",
        ["schedule_id", sa.text("scheduled_for DESC")],
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(64), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(40), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("clients", sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("clients", sa.Column("daily_run_limit", sa.Integer(), nullable=True))
    op.add_column("clients", sa.Column("monthly_budget_usd", sa.Numeric(10, 2), nullable=True))

    op.drop_index("idx_runs_one_pending_per_prompt_model", table_name="runs")
    op.create_index(
        "idx_runs_one_pending_per_prompt_model",
        "runs",
        ["prompt_id", "model_id", "persona_id", "market_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_runs_one_pending_per_prompt_model", table_name="runs")
    op.create_index(
        "idx_runs_one_pending_per_prompt_model",
        "runs",
        ["prompt_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.drop_column("clients", "monthly_budget_usd")
    op.drop_column("clients", "daily_run_limit")
    op.drop_column("clients", "priority")

    op.drop_table("notification_outbox")
    op.drop_table("worker_heartbeats")

    op.drop_index("idx_run_queue_schedule_history", table_name="run_queue")
    op.drop_index("idx_run_queue_claim_order", table_name="run_queue")
    op.drop_index("idx_run_queue_unique_occurrence", table_name="run_queue")
    op.drop_table("run_queue")

    op.drop_index("idx_run_schedules_next_run_at", table_name="run_schedules")
    op.drop_table("run_schedules")
