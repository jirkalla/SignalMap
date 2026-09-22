"""Client — the organization SignalMap is tracking AI perception for."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client_alias import ClientAlias
    from app.models.prompt import PromptSet
    from app.models.tracked_entity import TrackedEntity


class Client(Base):
    """A client/project. Phase 1: identification only (name, industry, notes).

    Strategy/reputation configuration (guiding principles, priority topics,
    desired wording) is deferred to the analysis-layer phase — see
    docs/REQUIREMENTS.md §2.1.

    `domain` (phase 3) is the client's own primary domain, e.g. 'acme.com' —
    used by the mention_visibility analysis skill to detect when the
    client's own site is among a run's cited sources. Nullable: a client
    without one simply has citation-matching skipped, not an error.
    """

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    industry: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(200))
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    """Marks a client whose runs exist only to validate the app against real providers.

    What it does: excludes the client's runs from every `/ops` aggregate by default
    (`app.services.ops_dashboard.ops_scoped_run_ids_query`), so run volume, success rate and the
    cost estimate describe real client work only. Because the flag is evaluated per query and
    nothing is written to any run, it applies to the client's WHOLE history — setting it removes
    past runs from those totals too, and clearing it brings them all back (docs/
    TASKS_PRE_SCHEDULER.md design decision 15).

    What it does NOT do: it never hides the client from anything. `/clients`, the client's own
    detail page, every client selector and `/dashboard` all show a test client exactly like any
    other, just with a "Test" badge (design decision 2). This is not an archival, soft-delete or
    lifecycle flag — `clients.is_active` was considered and deliberately rejected
    (docs/TASKS_SCHEDULER.md design decision 31), and "prospect / active / former client" is a
    property of the sales relationship that does not belong in this column either.

    Only an admin can change it, through `POST /clients/{id}/toggle-test` — never through the
    client form, whose unchecked checkbox would silently reset it (design decision 14).
    """
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    """Scheduler priority weight (docs/TASKS_SCHEDULER.md design decision 20). A queue item's
    priority is fixed at enqueue time as `client.priority * 1000 + schedule.priority` — later
    changes to this column only affect items enqueued afterwards, never ones already queued.
    """
    daily_run_limit: Mapped[int | None] = mapped_column(Integer)
    """Hard cap on runs per rolling 24h for this client, enforced in
    app/services/run_execution.py so it applies to scheduled and manual triggers alike (design
    decision 26). NULL = fall back to the configured default, not "unlimited".
    """
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    """Soft monthly spend threshold in USD (design decision 33) — crossing it only raises
    `budget.threshold_exceeded`, it never blocks a run. Computed from actual, already-incurred
    run costs (app/services/cost.py), not an estimate, since the real cost of a run is only known
    after it completes. NULL = no threshold configured.
    """
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prompt_sets: Mapped[list["PromptSet"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["ClientAlias"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    tracked_entities: Mapped[list["TrackedEntity"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
