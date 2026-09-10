"""Client — the organization SignalMap is tracking AI perception for."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client_alias import ClientAlias
    from app.models.prompt import PromptSet


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prompt_sets: Mapped[list["PromptSet"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["ClientAlias"]] = relationship(back_populates="client", cascade="all, delete-orphan")
