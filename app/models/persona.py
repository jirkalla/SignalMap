"""Persona — who the system_instruction template frames the asker as, e.g. "person" (default),

"manager", "politician". Kept as data (not hardcoded in the template string), same philosophy as
Market/Provider — see app/routers/settings.py's DEFAULT_SYSTEM_INSTRUCTION_TEMPLATE (CPH-T5).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Persona(Base):
    """One persona a run's system_instruction can frame the asker as.

    `label` is free text (e.g. "person", "manager", "politician") substituted directly into the
    English system_instruction template sent to the provider — it is content for the AI model,
    not developer-authored UI copy, so unlike most user-facing strings in this app it does not
    go through the DE/EN `t()` i18n layer (same reasoning as `Market.label`).

    Exactly one row has `is_default = True` at any time, enforced by a partial unique index
    (migration 0021) — the admin UI (CPH-T4) always clears the previous default before setting
    a new one, in the same transaction, so this invariant should never be violated by app code.
    """

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
