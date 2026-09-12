"""SQLAlchemy engine, session factory, and the FastAPI DB dependency."""

from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine/session, used only by the auth subsystem (app/auth.py) — fastapi-users requires
# an AsyncSession, while the rest of the app is synchronous throughout. psycopg (v3, already
# the project's driver) supports SQLAlchemy's asyncio engine over the same DATABASE_URL, so this
# needs no new dependency (verified against the running DB, docs/TASKS_PHASE6.md P6-T1) — a
# route stays a normal `def` and can still depend on an async dependency; FastAPI runs it on
# the event loop regardless of whether the route itself is sync or async.
async_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


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


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session — only for the auth subsystem (app/auth.py)."""
    async with AsyncSessionLocal() as db:
        yield db
