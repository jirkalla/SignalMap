"""Market — a language + country combination a prompt is run in."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Market(Base):
    """A language/country combination, e.g. 'de-DE'. Seeded, not user-editable in phase 1."""

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    country: Mapped[str | None] = mapped_column(String(10))
    label: Mapped[str | None] = mapped_column(String(100))
