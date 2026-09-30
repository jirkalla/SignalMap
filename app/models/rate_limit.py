"""Cross-process request-pacing state (code-review finding, 2026-09-30, docs/TASKS_CITATION_

VERIFICATION.md). `app/services/source_capture.py`'s per-domain throttle and
`app/services/archive_lookup.py`'s single-key throttle used to be plain in-process module
variables — production runs the worker as 4 separate processes (`docker compose --scale
worker=4`), each pacing itself independently against the same external domain/archive.org, which
could multiply the documented "1 request/second" (or "/3 seconds") politeness contract by however
many processes happened to hit the same key at once. This table gives every process the same
clock to pace against instead.
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ThrottleState(Base):
    """One row per throttled key (a domain, or `'archive.org'`) — `next_available_at` is the

    earliest moment a NEW reservation may be granted for this key. Not evidence (NFR-6 doesn't
    apply): a plain, mutable, best-effort pacing counter, overwritten in place via UPSERT on
    purpose, closer in spirit to a cache than a historical record.
    """

    __tablename__ = "throttle_state"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    next_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
