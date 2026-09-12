"""Admin-only user management CRUD, and the forced-password-change flow it triggers
(docs/TASKS_PHASE6.md P6-T8).
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import User


def test_admin_can_create_list_and_edit_a_user(admin_client: TestClient, db_session: Session):
    create_response = admin_client.post(
        "/users",
        data={"name": "New Person", "email": "new-person@test.local", "role": "editor", "password": "InitialPass123!"},
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    list_response = admin_client.get("/users")
    assert list_response.status_code == 200
    assert "New Person" in list_response.text

    user = db_session.query(User).filter_by(email="new-person@test.local").one()
    assert user.role == "editor"
    assert user.must_change_password is True  # a freshly admin-created account must change it (P6-T5)

    edit_response = admin_client.post(
        f"/users/{user.id}/edit", data={"name": "Renamed Person", "role": "viewer"}, follow_redirects=False
    )
    assert edit_response.status_code == 303
    db_session.refresh(user)
    assert user.name == "Renamed Person"
    assert user.role == "viewer"


def test_duplicate_email_returns_structured_conflict_not_500(admin_client: TestClient, editor_user: User):
    response = admin_client.post(
        "/users",
        data={"name": "Duplicate", "email": editor_user.email, "role": "viewer", "password": "SomePass123!"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "already exists" in response.text


def test_editor_gets_403_on_user_management(authed_client: TestClient):
    response = authed_client.get("/users")

    assert response.status_code == 403


def test_viewer_gets_403_on_user_management(viewer_client: TestClient):
    response = viewer_client.get("/users")

    assert response.status_code == 403


def test_must_change_password_flow_forces_redirect_then_clears_after_change(admin_client: TestClient):
    """A fresh admin-created account is redirected to /change-password on every request until it
    actually changes its password (P6-T5) — regardless of role or where it was headed.

    Uses a second, independent TestClient (not the `client`/`authed_client` fixtures) because
    `admin_client` is already logged in as the admin on this test's one shared cookie jar; the
    fresh account needs its own session to prove the redirect applies to it specifically, not to
    whoever happens to be logged in. It reuses the same dependency_overrides already installed on
    the shared `app` object by the `admin_client` fixture chain, so it still talks to the test DB.
    """
    create_response = admin_client.post(
        "/users",
        data={"name": "Fresh Account", "email": "fresh@test.local", "role": "viewer", "password": "FirstLogin123!"},
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    fresh_client = TestClient(app)
    login_response = fresh_client.post("/auth/login", data={"username": "fresh@test.local", "password": "FirstLogin123!"})
    assert login_response.status_code == 204

    redirected = fresh_client.get("/clients", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/change-password"

    change_response = fresh_client.post(
        "/change-password", data={"new_password": "BrandNewPass456!"}, follow_redirects=False
    )
    assert change_response.status_code == 303

    after_change = fresh_client.get("/clients", follow_redirects=False)
    assert after_change.status_code == 200
