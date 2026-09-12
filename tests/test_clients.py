"""Client CRUD (docs/REQUIREMENTS.md FR-1..FR-3) and its delete policy (HD-T4)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Client, Prompt, PromptSet, Run


def _create_client(authed_client: TestClient, name: str = "Acme Corporation") -> int:
    response = authed_client.post(
        "/clients", data={"name": name, "industry": "Automotive", "notes": "test notes"}, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


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
    run = Run(prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, status="success")
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
