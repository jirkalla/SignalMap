"""ClientAlias — an alternate name/spelling to match a client against."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client import Client


class ClientAlias(Base):
    """One alternate name/spelling for a client, used by the mention_visibility
    analysis skill alongside `Client.name` when matching a run's rendered text
    (e.g. 'Acme', 'Acme Corp', 'Acme GmbH' all pointing at the same client).

    Uniqueness is case-insensitive (a functional index on lower(alias), not a
    plain column UniqueConstraint) — the matching engine itself is
    case-insensitive, so 'Acme' and 'acme' would be the same alias for its
    purposes; the DB is the actual source of truth for this, not just the
    app-level pre-check in app/routers/clients.py (migration 0013).
    """

    __tablename__ = "client_aliases"
    __table_args__ = (
        Index(
            "uq_client_aliases_client_id_lower_alias",
            "client_id",
            text("lower(alias)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="aliases")
