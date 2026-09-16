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

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.market import Market
    from app.models.persona import Persona
    from app.models.prompt import Prompt
    from app.models.provider import AIModel
    from app.models.user import User


class Run(Base):
    """One execution of a prompt against one AI model.

    `market_id` defaults to the prompt's own market when a run is
    triggered, but can be overridden per run (e.g. to see how the same
    prompt text is framed for a different market) — it is recorded on the
    run itself rather than inferred from `prompt.market`, so historical
    runs stay accurate even if that ever diverges.

    `persona_id` is the same kind of per-run override, for who the
    system_instruction template frames the asker as (app/models/persona.py)
    — defaults to whichever persona is currently marked `is_default` at
    trigger time, but can be swapped per run (e.g. to compare how the same
    prompt is answered when framed as a "manager" vs. a "politician"), and
    is likewise recorded on the run itself so historical runs stay accurate
    even if the default persona changes later.

    `request_payload` records exactly what was sent to the provider (model,
    prompt text, system instruction), set before the adapter is called so
    it's present whether the run succeeds or fails.

    The partial unique index below backstops the "one pending run per
    prompt+model" check in app/routers/runs.py against a TOCTOU race
    (two concurrent triggers both passing the SELECT check before either
    INSERTs) — mirrored here at the ORM level, matching alembic migration
    0024, so `Base.metadata.create_all()` (what the test suite uses) creates
    the same constraint the migration creates against the real database.
    """

    __tablename__ = "runs"
    __table_args__ = (
        Index(
            "idx_runs_one_pending_per_prompt_model",
            "prompt_id",
            "model_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[int] = mapped_column(ForeignKey("prompts.id"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_models.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    persona_id: Mapped[int] = mapped_column(ForeignKey("personas.id"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Nullable: a future scheduler-triggered run (docs/ROADMAP.md §5) has no human behind it —
    # trigger_type='scheduled' + triggered_by_user_id=None together are unambiguous.
    triggered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    prompt: Mapped["Prompt"] = relationship(back_populates="runs")
    model: Mapped["AIModel"] = relationship()
    market: Mapped["Market"] = relationship()
    persona: Mapped["Persona"] = relationship()
    triggered_by: Mapped["User | None"] = relationship()
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
    search_queries: Mapped[list["SearchQuery"]] = relationship(
        back_populates="raw_response", cascade="all, delete-orphan"
    )


class Citation(Base):
    """One claim-source link the provider returned for a raw response.

    A row is one (answer segment, source) pair, not one source: a provider may
    cite the same URL for several segments, and several sources for one segment,
    and every such pair gets its own row.

    Two different texts are involved in a citation and they live in two separate
    columns (docs/TASKS_GEMINI_CITATIONS.md design decision 6):

    - `cited_answer_span` (plus `answer_span_start`/`answer_span_end`) is the
      span OF THE ANSWER that the source supports — the model's own claim,
      locatable in `RawResponse.rendered_text` via the offsets.
    - `source_passage` is the passage FROM THE SOURCE PAGE that the provider
      quoted as backing for that claim.

    Which provider fills which is not uniform, because the APIs expose different
    halves of the link:

    - Google Gemini — answer span + offsets (`grounding_supports[].segment`);
      no source passage (the API returns none).
    - OpenAI ChatGPT — answer span + offsets (`annotations[].start_index`/
      `end_index` sliced out of the answer text); no source passage.
    - Anthropic Claude — source passage only (`citations[].cited_text`). The
      offset columns stay permanently NULL there: `web_search_result_location`
      carries an `encrypted_index` into the search results, not an offset into
      the answer, so there is nothing to fill them with. That is a property of
      the API, not a gap to be closed later.
    """

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(String(300))
    source_domain: Mapped[str | None] = mapped_column(String(200))
    citation_position: Mapped[int | None] = mapped_column(Integer)
    cited_answer_span: Mapped[str | None] = mapped_column(Text)
    answer_span_start: Mapped[int | None] = mapped_column(Integer)
    answer_span_end: Mapped[int | None] = mapped_column(Integer)
    source_passage: Mapped[str | None] = mapped_column(Text)

    raw_response: Mapped["RawResponse"] = relationship(back_populates="citations")


class SearchQuery(Base):
    """One search query the provider issued while grounding a raw response."""

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_position: Mapped[int | None] = mapped_column(Integer)

    raw_response: Mapped["RawResponse"] = relationship(back_populates="search_queries")
