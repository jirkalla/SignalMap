"""DomainClassification — manual editorial classification of a cited domain."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

DOMAIN_TYPES = ("institutional", "editorial", "corporate", "reference", "ugc", "other")


class DomainClassification(Base):
    """One domain's editorial type (Institutional/Editorial/Corporate/Reference/UGC/Other).

    Keyed by normalized domain (app.utils.normalize_domain), not by citation row — one
    classification serves every citation of that domain across every client. Set manually in
    the dashboard league table, never inferred automatically (same caution applied to
    sentiment analysis — a wrong auto-classification is worse than an honest "unclassified").

    Deliberately has no "competitor" category — see docs/TASKS_PHASE5.md P5-T2 design
    decision 9: that's derivable from tracked_entities.domain (P5-T3), a second hand-maintained
    copy of the same information would be a duplicate source of truth.
    """

    __tablename__ = "domain_classifications"
    __table_args__ = (
        CheckConstraint(
            "domain_type IN ('" + "', '".join(DOMAIN_TYPES) + "')",
            name="ck_domain_classifications_domain_type",
        ),
    )

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    domain_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
