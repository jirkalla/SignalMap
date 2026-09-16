"""Provider and AIModel — which AI surfaces/models can be run against.

Kept as data (not hardcoded lists in code) so a new provider is a new row
plus a new adapter file under app/adapters/, per the signalmap-conventions
skill's "when adding a new AI provider" section.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Same idiom as User.ROLES / DomainClassification.DOMAIN_TYPES — adding a component type or unit
# means extending these tuples, not changing the framework. Migration 0025 hardcodes its own copy
# for the CHECK constraints (Alembic migrations don't import application code) — changing these
# later needs a new migration to ALTER those constraints.
COMPONENT_TYPES = ("input", "output", "cache_read", "cache_write", "cache_write_5m", "cache_write_1h")
UNITS = ("per_1m_tokens", "per_call")


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
    price_components: Mapped[list["AIModelPriceComponent"]] = relationship(
        back_populates="ai_model",
        order_by="AIModelPriceComponent.effective_from.desc()",
        cascade="all, delete-orphan",
    )


class AIModelPriceComponent(Base):
    """One priced component of an `AIModel`'s cost (input, output, a cache tier, ...), effective
    from a point in time onward.

    An open, per-provider set of components — Anthropic bills cache read plus two separate
    cache-write tiers, Gemini/OpenAI each bill one cache-read tier, and none of that fits two
    fixed columns (docs/TASKS_COST_COMPONENTS.md design decisions 1-4; this table replaced the
    project's original `AIModel.cost_per_1k_input_usd`/`cost_per_1k_output_usd` plus
    `AIModelPriceHistory`, both dropped in migration 0026 once nothing read them any more).
    Append-only: a new price is a new row with a later `effective_from`, never an edit to an
    existing row. The absence of a row for a given `(ai_model_id, component_type)` as of a given
    date means "we don't know this component's price", never "it's free" — a model that is
    genuinely free is marked via `AIModel.is_free` instead (design decision 14; same distinction
    `AIModel`'s own docstring draws for that flag).

    `price_per_unit_usd` is USD per 1M tokens (`unit='per_1m_tokens'`), not per 1k — Gemini
    3.1 flash-lite's cache-read price is $0.025/1M, which the project's original per-1k
    NUMERIC(10,5) column would have rounded to a value ~20% too high (design decision 11).
    `unit='per_call'` is not used by any row this project currently writes — reserved ahead of
    time for a future per-call search/tool-call fee (CC-9) so that work is a data change, not a
    schema migration.

    `__table_args__` mirrors migration 0025's CHECK constraints and index exactly: the test suite
    builds its schema via `Base.metadata.create_all`, not Alembic, so a constraint that exists
    only in the migration would silently not exist in tests (the same gap migration 0024's
    `idx_runs_one_pending_per_prompt_model` had to close at the ORM level, see `Run`'s docstring).
    """

    __tablename__ = "ai_model_price_components"
    __table_args__ = (
        CheckConstraint(
            "component_type IN ('" + "', '".join(COMPONENT_TYPES) + "')",
            name="ck_ai_model_price_components_component_type",
        ),
        CheckConstraint(
            "unit IN ('" + "', '".join(UNITS) + "')",
            name="ck_ai_model_price_components_unit",
        ),
        Index(
            "idx_price_components_lookup",
            "ai_model_id",
            "component_type",
            text("effective_from DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ai_model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False)
    component_type: Mapped[str] = mapped_column(String(30), nullable=False)
    price_per_unit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    unit: Mapped[str] = mapped_column(
        String(20), nullable=False, default="per_1m_tokens", server_default="per_1m_tokens"
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    ai_model: Mapped["AIModel"] = relationship(back_populates="price_components")
