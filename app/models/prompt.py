"""PromptSet and Prompt — the versioned questions asked of an AI provider."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.market import Market
    from app.models.run import Run


class PromptSet(Base):
    """A named group of prompts belonging to one client."""

    __tablename__ = "prompt_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="prompt_sets")
    prompts: Mapped[list["Prompt"]] = relationship(back_populates="prompt_set", cascade="all, delete-orphan")


class Prompt(Base):
    """A single question, tied to a market. `version` exists from phase 1 on

    (per FR-6 of docs/REQUIREMENTS.md) but there is no edit/version-bump UI
    yet — every prompt is created at version 1.
    """

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_set_id: Mapped[int] = mapped_column(ForeignKey("prompt_sets.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prompt_set: Mapped["PromptSet"] = relationship(back_populates="prompts")
    market: Mapped["Market"] = relationship()
    runs: Mapped[list["Run"]] = relationship(back_populates="prompt")
