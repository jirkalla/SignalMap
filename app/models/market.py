"""Market — a language + country combination a prompt is run in."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Market(Base):
    """A language/country combination, e.g. 'de-DE'.

    `locale_name` is the human-readable name of the locale as a whole
    (e.g. "Czech (Czech Republic)") — named after the i18n term "locale"
    (a language+region combination) specifically to avoid reading as
    "the country's name", which it isn't (it also carries the language).
    """

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    country: Mapped[str | None] = mapped_column(String(10))
    locale_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
