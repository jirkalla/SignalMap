"""TrackedEntity/TrackedEntityAlias — competitors tracked alongside a client for the
competitive_visibility analysis skill (docs/TASKS_PHASE5.md P5-T3).
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client import Client


class TrackedEntity(Base):
    """A competitor tracked alongside a client, for share-of-voice/position analysis.

    Deliberately has no row for the client itself — Client.name/ClientAlias (phase 3) already
    own that identity. Duplicating it here would create two independent sources of truth for
    "what is this client called" (docs/TASKS_PHASE5.md P5-T3 design decision 1). `domain` is
    optional and used the same way as Client.domain — citation-matching against this entity is
    simply skipped when it's not set, not an error.

    Uniqueness is case-insensitive (functional index on lower(name), like client_aliases after
    migration 0013) — the matching engine itself is case-insensitive, so the DB should be the
    source of truth for that, not just the app-level pre-check.
    """

    __tablename__ = "tracked_entities"
    __table_args__ = (
        Index(
            "uq_tracked_entities_client_id_lower_name",
            "client_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="tracked_entities")
    aliases: Mapped[list["TrackedEntityAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class TrackedEntityAlias(Base):
    """One alternate name/spelling for a tracked entity — the same role ClientAlias plays for
    the client itself, one level down (e.g. matching both 'UAE' and 'Vereinigte Arabische
    Emirate' for the same tracked entity).
    """

    __tablename__ = "tracked_entity_aliases"
    __table_args__ = (
        Index(
            "uq_tracked_entity_aliases_entity_id_lower_alias",
            "tracked_entity_id",
            text("lower(alias)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_entity_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_entities.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity: Mapped["TrackedEntity"] = relationship(back_populates="aliases")
