"""Run, RawResponse, Citation — the evidence trail for one prompt execution.

Run lifecycle is two-phase: a row is inserted with status='pending' before
the provider adapter is called, then updated in place to 'success'/'error'
once the call returns (or fails). This is a normal state transition on the
run's own control row, not a violation of the "never overwrite historical
rows" rule — that rule protects evidence rows (RawResponse, Citation),
which are only ever inserted once and never updated afterwards.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.market import Market
    from app.models.prompt import Prompt
    from app.models.provider import AIModel


class Run(Base):
    """One execution of a prompt against one AI model.

    `market_id` defaults to the prompt's own market when a run is
    triggered, but can be overridden per run (e.g. to see how the same
    prompt text is framed for a different market) — it is recorded on the
    run itself rather than inferred from `prompt.market`, so historical
    runs stay accurate even if that ever diverges.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[int] = mapped_column(ForeignKey("prompts.id"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    prompt: Mapped["Prompt"] = relationship(back_populates="runs")
    model: Mapped["AIModel"] = relationship()
    market: Mapped["Market"] = relationship()
    raw_response: Mapped["RawResponse | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )


class RawResponse(Base):
    """The complete, untouched provider response for one successful run,

    plus the rendered human-readable answer derived from it. Only created
    when a run succeeds — a failed run has no RawResponse, only
    Run.error_message.
    """

    __tablename__ = "raw_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    has_citations: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped["Run"] = relationship(back_populates="raw_response")
    citations: Mapped[list["Citation"]] = relationship(back_populates="raw_response", cascade="all, delete-orphan")


class Citation(Base):
    """One source the provider cited in a raw response."""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(String(300))
    source_domain: Mapped[str | None] = mapped_column(String(200))
    citation_position: Mapped[int | None] = mapped_column(Integer)
    cited_answer_span: Mapped[str | None] = mapped_column(Text)

    raw_response: Mapped["RawResponse"] = relationship(back_populates="citations")
