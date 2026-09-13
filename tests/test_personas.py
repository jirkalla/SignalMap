"""Persona admin CRUD (app/routers/personas.py) — single-default enforcement and delete policy."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Persona, Prompt, Run


def _create_persona(authed_client: TestClient, label: str, *, is_default: bool = False) -> None:
    data = {"label": label}
    if is_default:
        data["is_default"] = "true"
    response = authed_client.post("/personas", data=data, follow_redirects=False)
    assert response.status_code == 303


def test_create_list_edit_flow(authed_client: TestClient, db_session: Session, seed: dict):
    _create_persona(authed_client, "manager")

    list_response = authed_client.get("/personas")
    assert list_response.status_code == 200
    assert "manager" in list_response.text

    manager = db_session.query(Persona).filter_by(label="manager").one()

    edit_response = authed_client.post(
        f"/personas/{manager.id}/edit",
        data={"label": "senior manager"},
        follow_redirects=False,
    )
    assert edit_response.status_code == 303
    assert "senior manager" in authed_client.get("/personas").text


def test_setting_a_new_default_clears_the_old_one(authed_client: TestClient, db_session: Session, seed: dict):
    """Only the seeded "person" persona (seed fixture) is default at the start — switching the
    default to a new persona must clear it from "person", never leaving two (or zero) defaults.
    """
    _create_persona(authed_client, "manager")
    manager = db_session.query(Persona).filter_by(label="manager").one()
    assert manager.is_default is False
    assert seed["persona"].is_default is True

    response = authed_client.post(
        f"/personas/{manager.id}/edit",
        data={"label": "manager", "is_default": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.refresh(manager)
    db_session.refresh(seed["persona"])
    assert manager.is_default is True
    assert seed["persona"].is_default is False


def test_duplicate_label_is_rejected(authed_client: TestClient, seed: dict):
    response = authed_client.post("/personas", data={"label": seed["persona"].label})

    assert response.status_code == 409
    assert "already exists" in response.text


def test_blank_label_is_rejected(authed_client: TestClient):
    response = authed_client.post("/personas", data={"label": "   "})

    assert response.status_code == 409


def test_delete_blocked_on_the_current_default(authed_client: TestClient, db_session: Session, seed: dict):
    response = authed_client.post(f"/personas/{seed['persona'].id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert "default" in response.text.lower()
    assert db_session.get(Persona, seed["persona"].id) is not None


def test_unsetting_default_without_a_replacement_is_rejected(authed_client: TestClient, db_session: Session, seed: dict):
    """Unchecking is_default on the persona that currently is the default must be blocked the
    same way delete is — the table must never end up with zero default personas.
    """
    response = authed_client.post(
        f"/personas/{seed['persona'].id}/edit",
        data={"label": seed["persona"].label, "is_default": "false"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    db_session.refresh(seed["persona"])
    assert seed["persona"].is_default is True


def test_delete_blocked_when_a_run_exists_against_the_persona(
    authed_client: TestClient, db_session: Session, seed: dict
):
    from app.models import Client, PromptSet

    _create_persona(authed_client, "politician")
    politician = db_session.query(Persona).filter_by(label="politician").one()

    client_row = Client(name="Test Client", slug="test-persona-in-use")
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name="Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Q?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(
        prompt_id=prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=politician.id,
        status="success",
    )
    db_session.add(run)
    db_session.commit()

    response = authed_client.post(f"/personas/{politician.id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert db_session.get(Persona, politician.id) is not None


def test_delete_succeeds_for_an_unused_non_default_persona(authed_client: TestClient, db_session: Session):
    _create_persona(authed_client, "unused")
    persona = db_session.query(Persona).filter_by(label="unused").one()

    response = authed_client.post(f"/personas/{persona.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert db_session.get(Persona, persona.id) is None
