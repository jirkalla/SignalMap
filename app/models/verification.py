"""SourceText, SourceDocument, VerificationJob, CitationVerification — the source-snapshot and

verdict layer for citation verification (docs/TASKS_CITATION_VERIFICATION.md T3/T7, etapy B/C).

Two distinct kinds of evidence, split across two tables (design decision 12, "varianta A"):
`SourceText` is content-addressed by the sha256 of its extracted text, so the same page text
fetched via two different URLs (or the same URL fetched twice with no change) is stored once;
`SourceDocument` is one fetch attempt of one URL — always a new row (NFR-6, design decision 2),
even when its `text_sha256` points at a `SourceText` row that already existed. Raw HTML/PDF bytes
are never stored (design decision 12) — only the extracted text.

`VerificationJob` is the capture/judge work queue, the same `SKIP LOCKED` shape as
`app/models/schedule.py`'s `RunQueueItem` (claimed by `app/services/verification_queue.py`, T5) —
runs from `run_queue` always take priority over these (design decision 3).

`CitationVerification` is one verdict on one citation — append-only (NFR-6, design decision 2):
a better verifier, or a re-check after the source changed, adds a new row next to the old one,
never edits it. This module only defines schema — capture/extraction (T4), queue processing (T5),
quote matching (T8), and LLM judging (T11/T12) land in their own tasks.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.provider import AIModel
    from app.models.run import Citation, RawResponse
    from app.models.user import User

# Design decision 28's verdict enum — shared by every `check_type` (quote/llm/reachability),
# one CHECK constraint rather than a separate column per check kind. Same pattern as
# app/models/domain_classification.py's DOMAIN_TYPES: migration 0037 hardcodes its own
# independent copy of this tuple for the CHECK constraint (migrations must stay runnable
# against whatever the model looked like when written, so they never import application code) —
# changing the valid set here requires a new migration to ALTER that constraint too.
VERDICTS = (
    "verified_exact",
    "verified_normalized",
    "page_changed",
    "archive_only",
    "partially_found",
    "not_found",
    "llm_supported",
    "llm_partial",
    "llm_not_supported",
    "llm_contradicted",
    "unverifiable",
    "source_reachable",
)

# Only meaningful when verdict='unverifiable' — not enforced cross-column (design decision 28
# only asks for a CHECK on the column's own values, same scope as domain_classifications' single-
# column constraint, not a business rule spanning two columns).
UNVERIFIABLE_REASONS = (
    "http_403",
    "http_404",
    "http_5xx",
    "timeout",
    "bot_challenge",
    "robots",
    "too_large",
    "pdf_no_text",
    "no_checkable_text",
    "archive_unavailable",
)


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


class CitationVerification(Base):
    """One verdict on one citation — append-only (NFR-6, design decision 2): a re-check after

    the source changed, or a better verifier, adds a new row next to the old one and never edits
    it; the UI (T9) shows the newest row per citation, `(citation_id, created_at DESC)` below.

    `check_type` distinguishes the three ways a verdict gets produced: `'quote'` (T8's
    deterministic literal-match check — Anthropic/Perplexity, free, always runs),
    `'llm'` (T12's paraphrase judgement — OpenAI/Gemini, costs money, gated by
    `clients.auto_verify_citations`), `'reachability'` (xAI — the source URL was reachable, but
    xAI's API gives no claim to check it against, so `verdict='source_reachable'` is the only
    thing a reachability check can ever conclude).

    `claim_text`/`claim_method` are `derive_claim`'s output (app/services/claims.py, T1),
    persisted here for the first time — T1/T2 only ever compute it at display/export time,
    never store it (design decision 5). `source_document_id` is nullable: a verdict of
    `verdict='unverifiable'` with a `robots`/`bot_challenge`/... reason may have no successful
    capture to point at (`source_documents.error_reason` explains the capture side; this row's
    own `reason` mirrors it for querying/aggregation without a join, design decision 28).

    `llm_*`/`tokens_*`/`cost_usd` are all NULL for `check_type != 'llm'` — one wide table rather
    than a subtype hierarchy, since only quote-check rows ever need `similarity`/`fragments` and
    only LLM rows ever need `llm_reason`/`cost_usd`, and every row is always exactly one kind, not
    a mix.
    """

    __tablename__ = "citation_verifications"
    __table_args__ = (
        CheckConstraint("check_type IN ('quote', 'llm', 'reachability')", name="ck_citation_verifications_check_type"),
        CheckConstraint("verdict IN ('" + "', '".join(VERDICTS) + "')", name="ck_citation_verifications_verdict"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('" + "', '".join(UNVERIFIABLE_REASONS) + "')",
            name="ck_citation_verifications_reason",
        ),
        Index("idx_citation_verifications_citation_created", "citation_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    citation_id: Mapped[int] = mapped_column(ForeignKey("citations.id", ondelete="CASCADE"), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    claim_text: Mapped[str | None] = mapped_column(Text)
    claim_method: Mapped[str | None] = mapped_column(String(25))
    check_type: Mapped[str] = mapped_column(String(15), nullable=False)
    verdict: Mapped[str] = mapped_column(String(25), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(30))
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    matched_text: Mapped[str | None] = mapped_column(Text)
    match_start: Mapped[int | None] = mapped_column(Integer)
    match_end: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[Any | None] = mapped_column(JSONB)
    fragments: Mapped[Any | None] = mapped_column(JSONB)
    llm_model_id: Mapped[int | None] = mapped_column(ForeignKey("ai_models.id"))
    llm_reason: Mapped[str | None] = mapped_column(Text)
    llm_quote: Mapped[str | None] = mapped_column(Text)
    llm_quote_found: Mapped[bool | None] = mapped_column(Boolean)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    verifier_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    citation: Mapped["Citation"] = relationship()
    source_document: Mapped["SourceDocument | None"] = relationship()
    llm_model: Mapped["AIModel | None"] = relationship()
