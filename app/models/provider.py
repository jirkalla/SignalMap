"""Provider and AIModel — which AI surfaces/models can be run against.

Kept as data (not hardcoded lists in code) so a new provider is a new row
plus a new adapter file under app/adapters/, per the signalmap-conventions
skill's "when adding a new AI provider" section.
"""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Provider(Base):
    """An AI provider surface, e.g. Google Gemini. Phase 1 has exactly one."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    ai_models: Mapped[list["AIModel"]] = relationship(back_populates="provider")


class AIModel(Base):
    """A specific model under a provider (e.g. gemini-3.5-flash).

    `model_name` is the exact string passed to the provider adapter — never
    hardcoded in adapter code, always looked up from here.
    """

    __tablename__ = "ai_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(150))
    capability_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_per_1k_input_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    cost_per_1k_output_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    supports_web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)

    provider: Mapped["Provider"] = relationship(back_populates="ai_models")
