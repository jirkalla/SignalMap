"""ClientAlias — an alternate name/spelling to match a client against."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client import Client


class ClientAlias(Base):
    """One alternate name/spelling for a client, used by the mention_visibility
    analysis skill alongside `Client.name` when matching a run's rendered text
    (e.g. 'Acme', 'Acme Corp', 'Acme GmbH' all pointing at the same client).
    """

    __tablename__ = "client_aliases"
    __table_args__ = (UniqueConstraint("client_id", "alias", name="uq_client_aliases_client_id_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="aliases")
