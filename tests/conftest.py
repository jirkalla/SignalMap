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
from fastapi_users.password import PasswordHelper
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters import register_adapter
from app.config import get_settings
from app.database import get_async_db, get_db
from app.main import app
from app.models import AIModel, AnalysisSkill, Base, Client, Market, Prompt, Provider, PromptSet, User
from tests.fake_adapter import FakeAdapter


def _test_database_url() -> str:
    """The same DATABASE_URL as the app, with the path swapped to signalmap_test."""
    parts = urlsplit(get_settings().database_url)
    return urlunsplit((parts.scheme, parts.netloc, "/signalmap_test", parts.query, parts.fragment))


engine = create_engine(_test_database_url())
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# A second, async engine against the SAME test database — needed because the auth subsystem
# (app/auth.py) reads/writes users exclusively through app.database.get_async_db, which by
# default points at the app's own DATABASE_URL, not signalmap_test. Without overriding it too
# (see the `client` fixture below), a real login POST during a test would look the seeded user
# up in the wrong database and always fail with LOGIN_BAD_CREDENTIALS.
async_engine = create_async_engine(_test_database_url())
AsyncTestSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# Every seeded test user shares one known password — the tests that need to actually log in
# (authed_client/admin_client/viewer_client below, or a test logging in manually) all use this
# same literal rather than each inventing their own.
TEST_USER_PASSWORD = "TestPass123!"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create every table once for the test session, drop them all when it ends."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _patch_auth_session_local(monkeypatch):
    """`current_user_from_cookie` (app/auth.py) reads the logged-in user through a direct
    `SessionLocal()` call, not FastAPI's dependency injection — it also runs inside
    `enforce_password_change` (app/main.py), which executes before routing/DI even starts. The
    `get_db` override in the `client` fixture below has no effect on it, so without this it would
    always resolve to the app's own dev database, find no matching user, and silently treat every
    logged-in test request as anonymous (current_user always None, must_change_password redirect
    never firing) — same misdirected-database trap `client`/`get_async_db` already had to be
    patched around.
    """
    monkeypatch.setattr("app.auth.SessionLocal", TestSessionLocal)


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
    """A TestClient whose get_db AND get_async_db dependencies are overridden to use this test's
    database — the async override matters even for tests that never touch auth directly, since
    every login-gated route re-resolves `current_active_user` (async) on every request.
    """

    def _override_get_db():
        yield db_session

    async def _override_get_async_db():
        async with AsyncTestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_async_db] = _override_get_async_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_user(db_session: Session, *, email: str, name: str, role: str) -> User:
    """A real account with a validly hashed password — the same synchronous PasswordHelper +
    direct User(...) construction every account-creation path in this app already uses
    (app/routers/users.py, scripts/create_admin.py, scripts/seed_dev_users.py), not fastapi-users'
    async UserManager.create(). That flow expects a UserCreate schema shaped around its own base
    fields (email/password) and has no slot for this project's required name/role columns without
    a custom schema — sticking to the app's own established sync pattern avoids that mismatch and
    an unnecessary event-loop bridge in a fixture, for the exact same reason those other three
    call sites never use it either.
    """
    user = User(
        email=email,
        hashed_password=PasswordHelper().hash(TEST_USER_PASSWORD),
        name=name,
        role=role,
        must_change_password=False,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    return _seed_user(db_session, email="admin@test.local", name="Admin User", role="admin")


@pytest.fixture
def editor_user(db_session: Session) -> User:
    return _seed_user(db_session, email="editor@test.local", name="Editor User", role="editor")


@pytest.fixture
def viewer_user(db_session: Session) -> User:
    return _seed_user(db_session, email="viewer@test.local", name="Viewer User", role="viewer")


def _login(client: TestClient, user: User) -> TestClient:
    response = client.post("/auth/login", data={"username": user.email, "password": TEST_USER_PASSWORD})
    assert response.status_code == 204, f"login as {user.email} failed: {response.status_code} {response.text}"
    return client


@pytest.fixture
def authed_client(client: TestClient, editor_user: User) -> TestClient:
    """The default logged-in client for existing tests that don't care which role they run as —
    editor, since that's the role every pre-phase-6 mutating test implicitly assumed (full CRUD,
    no admin-only or read-only restriction).
    """
    return _login(client, editor_user)


@pytest.fixture
def admin_client(client: TestClient, admin_user: User) -> TestClient:
    return _login(client, admin_user)


@pytest.fixture
def viewer_client(client: TestClient, viewer_user: User) -> TestClient:
    return _login(client, viewer_user)


@pytest.fixture
def seed(db_session: Session) -> dict:
    """Minimal rows every prompt/run/admin test needs: one market, both providers with one model
    each, and the mention_visibility/competitive_visibility analysis skills.

    Both providers are seeded (not just google_gemini) so provider/ai-model
    admin tests, and a run against either provider, all have real FK targets
    without each test building its own — see P2-T6. Both analysis skills are
    seeded here too (not just by migrations 0011/0017) because the test database
    is built via Base.metadata.create_all, never via Alembic — without this,
    _run_active_analysis_skills would silently find zero active skills.
    """
    market = Market(code="en-US", language="en", country="US", locale_name="English (United States)")
    provider = Provider(code="google_gemini", name="Google Gemini")
    anthropic_provider = Provider(code="anthropic", name="Anthropic Claude")
    analysis_skill = AnalysisSkill(
        key="mention_visibility",
        name="Mention & Visibility Detection",
        version=1,
        execution_type="rule_based",
        is_active=True,
    )
    competitive_visibility_skill = AnalysisSkill(
        key="competitive_visibility",
        name="Competitive Visibility Detection",
        version=1,
        execution_type="rule_based",
        is_active=True,
    )
    db_session.add_all([market, provider, anthropic_provider, analysis_skill, competitive_visibility_skill])
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
    db_session.refresh(analysis_skill)
    db_session.refresh(competitive_visibility_skill)
    return {
        "market": market,
        "provider": provider,
        "model": model,
        "anthropic_provider": anthropic_provider,
        "anthropic_model": anthropic_model,
        "analysis_skill": analysis_skill,
        "competitive_visibility_skill": competitive_visibility_skill,
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
