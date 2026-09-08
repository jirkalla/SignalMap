"""SystemInstructionTemplate — per-provider editable locale-framing hint.

Only meaningful for providers without real geographic/language API
targeting (Gemini's Google Search grounding has none — see
app/adapters/google.py). A provider with no row here falls back to the
built-in default in app.routers.runs; a row with an empty template means
that provider explicitly gets no system_instruction at all.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.provider import Provider


class SystemInstructionTemplate(Base):
    """One provider's editable system-instruction template, managed via /settings."""

    __tablename__ = "system_instruction_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False, unique=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped["Provider"] = relationship()
