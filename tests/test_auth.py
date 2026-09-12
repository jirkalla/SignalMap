"""Login/logout flow and the app-wide login gate (docs/TASKS_PHASE6.md P6-T8)."""

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models import Client, User
from tests.conftest import TEST_USER_PASSWORD


def test_login_with_valid_credentials_sets_the_session_cookie(client: TestClient, editor_user: User):
    response = client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})

    assert response.status_code == 204
    assert "signalmap_session" in response.cookies


def test_login_with_wrong_password_is_rejected(client: TestClient, editor_user: User):
    response = client.post("/auth/login", data={"username": editor_user.email, "password": "wrong-password"})

    assert response.status_code == 400
    assert response.json()["detail"] == "LOGIN_BAD_CREDENTIALS"


def test_login_with_unknown_email_is_rejected(client: TestClient):
    response = client.post("/auth/login", data={"username": "nobody@test.local", "password": "whatever123"})

    assert response.status_code == 400


def test_logout_invalidates_the_session_cookie(client: TestClient, editor_user: User):
    login = client.post("/auth/login", data={"username": editor_user.email, "password": TEST_USER_PASSWORD})
    assert login.status_code == 204
    assert client.get("/clients").status_code == 200

    logout = client.post("/auth/logout")
    assert logout.status_code == 204

    after_logout = client.get("/clients", follow_redirects=False)
    assert after_logout.status_code == 303
    assert after_logout.headers["location"] == "/login"


def test_unauthenticated_request_to_a_protected_route_redirects_to_login(client: TestClient):
    response = client.get("/clients", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unauthenticated_dashboard_api_request_returns_json_401_not_a_redirect(
    client: TestClient, db_session: Session
):
    """docs/TASKS_PHASE6.md design decision 6 — the dashboard's own /api/* endpoints are fetched
    via JS, so a redirect response would just be an opaque failure to the caller; they must keep
    getting a plain JSON 401 a fetch() call can actually branch on.
    """
    client_row = Client(name="Auth Test Client", slug="auth-test-client")
    db_session.add(client_row)
    db_session.commit()
    db_session.refresh(client_row)

    response = client.get(f"/dashboard/api/summary?client_id={client_row.id}", follow_redirects=False)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
