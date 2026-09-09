"""Shared pytest fixtures: an isolated test database, a wired-up TestClient,

minimal seed data, and the FakeAdapter test double.

Design decision 5 (docs/TASKS_HARDENING.md): tests run against a second
real Postgres database (signalmap_test) on the same compose container, not
SQLite — the app uses JSONB/GIN indexes that SQLite can't faithfully
emulate. The database itself must already exist (see README.md "Running
tests") — this file only creates/drops tables in it, never the database.
"""

from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.adapters import register_adapter
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import AIModel, Base, Client, Market, Prompt, Provider, PromptSet
from tests.fake_adapter import FakeAdapter


def _test_database_url() -> str:
    """The same DATABASE_URL as the app, with the path swapped to signalmap_test."""
    parts = urlsplit(get_settings().database_url)
    return urlunsplit((parts.scheme, parts.netloc, "/signalmap_test", parts.query, parts.fragment))


engine = create_engine(_test_database_url())
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create every table once for the test session, drop them all when it ends."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _fake_adapter_registered():
    """Swap both real adapters for FakeAdapter for the whole test session.

    Never calls the real Google or Anthropic API — see
    app/adapters/__init__.py's register_adapter(), the test seam this
    relies on.
    """
    register_adapter("google_gemini", FakeAdapter)
    register_adapter("anthropic", FakeAdapter)


@pytest.fixture(autouse=True)
def _reset_fake_adapter():
    """Every test starts with a clean FakeAdapter — no leftover payload/error from a previous test."""
    yield
    FakeAdapter.payload_to_return = None
    FakeAdapter.error_to_raise = None


@pytest.fixture(autouse=True)
def _truncate_tables():
    """Empty every table after each test so tests never see another test's data."""
    yield
    with engine.begin() as conn:
        table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db_session() -> Session:
    """One session per test, shared by the test's direct DB setup and every TestClient request."""
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> TestClient:
    """A TestClient whose get_db dependency is overridden to use this test's db_session."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def seed(db_session: Session) -> dict:
    """Minimal rows every prompt/run/admin test needs: one market, and both providers with one model each.

    Both providers are seeded (not just google_gemini) so provider/ai-model
    admin tests, and a run against either provider, all have real FK targets
    without each test building its own — see P2-T6.
    """
    market = Market(code="en-US", language="en", country="US", locale_name="English (United States)")
    provider = Provider(code="google_gemini", name="Google Gemini")
    anthropic_provider = Provider(code="anthropic", name="Anthropic Claude")
    db_session.add_all([market, provider, anthropic_provider])
    db_session.flush()
    model = AIModel(
        provider_id=provider.id,
        model_name="gemini-test-model",
        capability_tier="standard",
        is_active=True,
    )
    anthropic_model = AIModel(
        provider_id=anthropic_provider.id,
        model_name="claude-test-model",
        capability_tier="economy",
        is_active=True,
    )
    db_session.add_all([model, anthropic_model])
    db_session.commit()
    db_session.refresh(market)
    db_session.refresh(provider)
    db_session.refresh(model)
    db_session.refresh(anthropic_provider)
    db_session.refresh(anthropic_model)
    return {
        "market": market,
        "provider": provider,
        "model": model,
        "anthropic_provider": anthropic_provider,
        "anthropic_model": anthropic_model,
    }


@pytest.fixture
def sample_prompt(db_session: Session, seed: dict) -> Prompt:
    """A minimal Client -> PromptSet -> Prompt chain, for tests that need a prompt to run/delete against."""
    client_row = Client(name="Test Client", slug="test-client")
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name="Test Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="What is this test about?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return prompt
