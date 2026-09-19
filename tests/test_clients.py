"""Client CRUD (docs/REQUIREMENTS.md FR-1..FR-3) and its delete policy (HD-T4)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Client, Prompt, PromptSet, Run, User
from tests.conftest import TEST_USER_PASSWORD


def _create_client(authed_client: TestClient, name: str = "Acme Corporation") -> int:
    response = authed_client.post(
        "/clients", data={"name": name, "industry": "Automotive", "notes": "test notes"}, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _login_as(client: TestClient, user: User) -> None:
    """Re-authenticate an existing TestClient as `user`, replacing whatever session cookie it holds.

    Needed by the role-switching tests below: `admin_client` and `authed_client` are both built on
    the same function-scoped `client` fixture, so taking both in one test gives a single session
    logged in as whichever was resolved last, not two side-by-side sessions.
    """
    response = client.post("/auth/login", data={"username": user.email, "password": TEST_USER_PASSWORD})
    assert response.status_code == 204, f"login as {user.email} failed: {response.status_code}"


def test_create_list_edit_detail_flow(authed_client: TestClient):
    client_id = _create_client(authed_client)

    list_response = authed_client.get("/clients")
    assert list_response.status_code == 200
    assert "Acme Corporation" in list_response.text

    edit_response = authed_client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corp", "industry": "Auto", "notes": ""},
        follow_redirects=False,
    )
    assert edit_response.status_code == 303

    detail_response = authed_client.get(f"/clients/{client_id}")
    assert detail_response.status_code == 200
    assert "Acme Corp" in detail_response.text


def test_delete_blocked_when_a_run_exists_under_the_client(authed_client: TestClient, db_session: Session, seed: dict):
    client_id = _create_client(authed_client)
    prompt_set = PromptSet(client_id=client_id, name="Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Q?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(
        prompt_id=prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        status="success",
    )
    db_session.add(run)
    db_session.commit()

    response = authed_client.post(f"/clients/{client_id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert "1 run" in response.text
    assert db_session.get(Client, client_id) is not None


def test_delete_succeeds_for_a_client_with_no_runs(authed_client: TestClient):
    client_id = _create_client(authed_client, name="Empty Co")

    response = authed_client.post(f"/clients/{client_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/clients"
    assert authed_client.get(f"/clients/{client_id}").status_code == 404


# --- PRE-1: the is_test flag, admin-only and never written by the client form --------------------


def test_toggle_test_flips_the_flag_for_an_admin(admin_client: TestClient, db_session: Session):
    client_id = _create_client(admin_client)
    assert db_session.get(Client, client_id).is_test is False

    response = admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/clients/{client_id}"
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is True

    admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is False, "the flag must be reversible, not one-way"


def test_toggle_test_is_forbidden_for_an_editor(authed_client: TestClient, db_session: Session):
    """`authed_client` is an editor. Editors create and edit clients, but may not decide what counts
    into the ops figures (docs/TASKS_PRE_SCHEDULER.md design decision 14).
    """
    client_id = _create_client(authed_client)
    response = authed_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    assert response.status_code == 403
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is False


def test_toggle_test_is_forbidden_for_a_viewer(viewer_client: TestClient, authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    assert viewer_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False).status_code == 403


def test_editing_a_client_never_clears_the_test_flag(
    client: TestClient, admin_user: User, editor_user: User, db_session: Session
):
    """The regression decision 14 exists to prevent: an unchecked HTML checkbox is not submitted at
    all, so had `is_test` been a field on the shared client form (hidden from editors), an editor's
    first ordinary save would have silently reset it to false — quietly putting the test client's
    whole history back into the ops totals with nobody touching the flag.
    """
    _login_as(client, editor_user)
    client_id = _create_client(client)

    _login_as(client, admin_user)
    client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(Client, client_id).is_test is True

    _login_as(client, editor_user)
    response = client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Renamed by an editor", "industry": "Automotive", "notes": "edited", "domain": "acme.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    updated = db_session.get(Client, client_id)
    assert updated.name == "Renamed by an editor", "the edit itself must still go through"
    assert updated.is_test is True, "an ordinary client edit must not touch is_test"


def test_test_client_stays_visible_in_lists_with_a_badge(admin_client: TestClient, db_session: Session):
    """The flag hides a client from the ops AGGREGATES, never from the lists (design decision 2)."""
    client_id = _create_client(admin_client, name="Test Skoda")
    admin_client.post(f"/clients/{client_id}/toggle-test", follow_redirects=False)

    list_response = admin_client.get("/clients")
    assert list_response.status_code == 200
    assert "Test Skoda" in list_response.text

    detail_response = admin_client.get(f"/clients/{client_id}")
    assert detail_response.status_code == 200
    assert "Test Skoda" in detail_response.text


def test_toggle_is_offered_to_an_admin_and_hidden_from_an_editor(
    client: TestClient, admin_user: User, editor_user: User
):
    """`can_flag_test_client` checked through the rendered page — the admin gets the toggle, the
    editor doesn't. UI only; the 403 tests above cover the enforcement that actually matters.
    """
    _login_as(client, editor_user)
    client_id = _create_client(client)
    toggle_action = f"/clients/{client_id}/toggle-test"
    assert toggle_action not in client.get(f"/clients/{client_id}").text

    _login_as(client, admin_user)
    assert toggle_action in client.get(f"/clients/{client_id}").text
