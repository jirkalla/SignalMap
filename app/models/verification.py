"""SourceText, SourceDocument, VerificationJob — the source-snapshot layer for citation

verification (docs/TASKS_CITATION_VERIFICATION.md T3, etapa B).

Two distinct kinds of evidence, split across two tables (design decision 12, "varianta A"):
`SourceText` is content-addressed by the sha256 of its extracted text, so the same page text
fetched via two different URLs (or the same URL fetched twice with no change) is stored once;
`SourceDocument` is one fetch attempt of one URL — always a new row (NFR-6, design decision 2),
even when its `text_sha256` points at a `SourceText` row that already existed. Raw HTML/PDF bytes
are never stored (design decision 12) — only the extracted text.

`VerificationJob` is the capture/judge work queue, the same `SKIP LOCKED` shape as
`app/models/schedule.py`'s `RunQueueItem` (claimed by `app/services/verification_queue.py`, T5) —
runs from `run_queue` always take priority over these (design decision 3).

This module only defines schema — capture, extraction, and queue processing land in later tasks
(T4/T5).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.run import RawResponse
    from app.models.user import User


class SourceText(Base):
    """Content-addressed extracted text of one page/PDF snapshot — the sha256 of `text` is its

    own primary key, so identical text fetched via different URLs, or the same URL fetched more
    than once with no change, is stored exactly once (design decision 12). Never the raw
    HTML/PDF bytes, only the extracted text (design decision 12's "varianta A" — raw storage is
    an explicitly deferred "varianta B", not built here).
    """

    __tablename__ = "source_texts"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    chars: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceDocument(Base):
    """One fetch attempt of one URL — live or from archive.org — always a new row, never updated

    or overwritten (NFR-6, design decision 2): a second capture of the same URL is a new piece of
    evidence, not a correction of the first. `text_sha256` is NULL when the fetch failed before
    any text could be extracted (`error_reason` explains why); a successful fetch whose text
    happens to match an already-stored `SourceText` still gets its own `SourceDocument` row here.

    `requested_url` is kept exactly as the citation returned it (Gemini's
    `vertexaisearch.cloud.google.com/grounding-api-redirect/...` redirect, OpenAI's URL with
    `utm_source=openai`, etc.) — `final_url` is where it actually landed after following
    redirects, resolved at capture time (design decision 6). Google's terms forbid modifying or
    display-substituting the original link, so `requested_url` — not `final_url` — is what the UI
    must keep showing (T9).

    `method='archive'` rows (T10) carry `archive_timestamp`, the Wayback CDX snapshot timestamp
    they came from; NULL on every `method='live'` row.
    """

    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint("method IN ('live', 'archive')", name="ck_source_documents_method"),
        Index("idx_source_documents_url_fetched", "requested_url", text("fetched_at DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    archive_timestamp: Mapped[str | None] = mapped_column(String(14))
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    # Open-ended reason code, not a CHECK-constrained enum (same choice as RunQueueItem.skip_reason,
    # app/models/schedule.py) — design decision 28's unverifiable reasons (http_403, http_404,
    # http_5xx, timeout, bot_challenge, robots, too_large, pdf_no_text, no_checkable_text,
    # archive_unavailable), but capture (T4) is where new ones get added if real traffic needs them.
    error_reason: Mapped[str | None] = mapped_column(String(40))
    challenge_vendor: Mapped[str | None] = mapped_column(String(40))
    text_sha256: Mapped[str | None] = mapped_column(ForeignKey("source_texts.sha256"))
    page_starts: Mapped[Any | None] = mapped_column(JSONB)
    locations: Mapped[Any | None] = mapped_column(JSONB)
    bytes: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # No default — every insert must say which version of the capture/extraction logic produced
    # it, the same way a better verifier later adds a new row instead of editing this one.
    verifier_version: Mapped[str] = mapped_column(String(20), nullable=False)

    source_text: Mapped["SourceText | None"] = relationship()


class VerificationJob(Base):
    """One queued unit of source-verification work: capture a source, or judge a claim against it

    (design decision 1). Claimed with `FOR UPDATE SKIP LOCKED`, the same pattern
    `app/services/queue.py`'s `claim_next` already uses for `run_queue` — processed in
    `app/services/verification_queue.py` (T5), which always drains `run_queue` first (design
    decision 3): a run must never wait on verification work.

    `raw_response_id` cascades on delete, same as `Citation` (app/models/run.py) — a job is
    derived-from, not independent of, the response it verifies.

    `requested_by_user_id` is NULL for the automatic capture job every successful run enqueues
    (T5); set only when a human explicitly asked for it — the "Ověřit citace" button (T13) or a
    manual re-judge — so the two origins stay distinguishable without a separate `source` column
    (unlike `RunQueueItem.source`, which needed one to tell schedule/manual/batch apart; here
    there are only two origins and one of them is exactly "not NULL").
    """

    __tablename__ = "verification_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('capture', 'judge')", name="ck_verification_jobs_kind"),
        CheckConstraint(
            "status IN ('queued', 'leased', 'done', 'error', 'deferred')",
            name="ck_verification_jobs_status",
        ),
        Index(
            "idx_verification_jobs_claim_order",
            text("priority DESC"),
            "scheduled_for",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(
        ForeignKey("raw_responses.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued", server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    leased_by: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    raw_response: Mapped["RawResponse"] = relationship()
    requested_by: Mapped["User | None"] = relationship()
