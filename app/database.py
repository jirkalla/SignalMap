"""SQLAlchemy engine, session factory, and the FastAPI DB dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for the duration of one request.

    Used as a FastAPI dependency (`db: Session = Depends(get_db)`) so every
    route gets its own session that is always closed afterwards, even if
    the route raises.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
