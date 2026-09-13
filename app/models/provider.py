"""Provider and AIModel — which AI surfaces/models can be run against.

Kept as data (not hardcoded lists in code) so a new provider is a new row
plus a new adapter file under app/adapters/, per the signalmap-conventions
skill's "when adding a new AI provider" section.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Provider(Base):
    """An AI provider surface, e.g. Google Gemini or Anthropic Claude."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ai_models: Mapped[list["AIModel"]] = relationship(back_populates="provider")


class AIModel(Base):
    """A specific model under a provider (e.g. gemini-3.5-flash).

    `model_name` is the exact string passed to the provider adapter — never
    hardcoded in adapter code, always looked up from here.

    `is_free` is an explicit fact, not derived from the price columns being
    zero/NULL — same discipline as `RawResponse.has_citations` (FR-13):
    never leave "is this actually free" ambiguous with "price not entered
    yet". See docs/TASKS_PHASE2.md design decision 5 for why phase-2's
    Gemini/Anthropic seed rows all ship with is_free = False.
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
    context_window_tokens: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    supports_web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped["Provider"] = relationship(back_populates="ai_models")
    price_history: Mapped[list["AIModelPriceHistory"]] = relationship(
        back_populates="ai_model",
        order_by="AIModelPriceHistory.effective_from.desc()",
        cascade="all, delete-orphan",
    )


class AIModelPriceHistory(Base):
    """One recorded price for an `AIModel`, effective from a point in time onward.

    Append-only — a new row is added only when `AIModel.cost_per_1k_*_usd` actually changes
    (`app/routers/ai_models.py`), never edited or deleted afterwards. Only `effective_from` is
    stored; a row's validity end is always the next row's `effective_from` for the same model
    (or "now" for the latest row) — computed where needed, not stored, so there is no second
    value that could drift out of sync with it (see migration 0020's docstring).
    """

    __tablename__ = "ai_model_price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False)
    cost_per_1k_input_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    cost_per_1k_output_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    ai_model: Mapped["AIModel"] = relationship(back_populates="price_history")
