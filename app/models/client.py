"""Client — the organization SignalMap is tracking AI perception for."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text, func
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
