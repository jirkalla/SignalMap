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
    """A single question, tied to a market, at a specific version.

    Editing a prompt never mutates this row (NFR-6) — it creates a new
    `Prompt` row at `version + 1` and flips this row's `is_current_version`
    to False. `root_prompt_id` links every version of the same logical
    prompt together: NULL on the original (version 1), pointing at that
    original row's id on every later version — so the whole lineage is
    `WHERE id = root_id OR root_prompt_id = root_id`.

    `is_active` is a separate, user-controlled concern (offer this prompt
    for new runs or not) — orthogonal to whether it's the current version.
    A run's `prompt_id` always points at one exact version, so historical
    runs keep showing the precise text they actually ran against even
    after later edits.
    """

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_set_id: Mapped[int] = mapped_column(ForeignKey("prompt_sets.id", ondelete="CASCADE"), nullable=False)
    root_prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_current_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prompt_set: Mapped["PromptSet"] = relationship(back_populates="prompts")
    market: Mapped["Market"] = relationship()
    runs: Mapped[list["Run"]] = relationship(back_populates="prompt")
