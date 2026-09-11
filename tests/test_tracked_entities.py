"""Tracked entity (competitor) and their alias management (docs/TASKS_PHASE5.md P5-T4) — data
feeding the competitive_visibility analysis skill's matching.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TrackedEntity, TrackedEntityAlias


def _create_client(client: TestClient, name: str = "Acme Corporation") -> int:
    response = client.post(
        "/clients",
        data={"name": name, "industry": "Automotive", "notes": "", "domain": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_add_and_delete_tracked_entity(client: TestClient, db_session: Session):
    client_id = _create_client(client)

    add_response = client.post(
        f"/clients/{client_id}/tracked-entities",
        data={"name": "Volkswagen", "domain": "vw.com"},
        follow_redirects=False,
    )
    assert add_response.status_code == 303

    entities = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()
    assert len(entities) == 1
    assert entities[0].name == "Volkswagen"
    assert entities[0].domain == "vw.com"

    delete_response = client.post(
        f"/clients/{client_id}/tracked-entities/{entities[0].id}/delete", follow_redirects=False
    )
    assert delete_response.status_code == 303
    assert db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all() == []


def test_tracked_entity_domain_is_optional(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)

    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()
    assert entity.domain is None


def test_duplicate_tracked_entity_name_returns_structured_conflict_not_500(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)

    response = client.post(
        f"/clients/{client_id}/tracked-entities", data={"name": "volkswagen"}, follow_redirects=False
    )

    assert response.status_code == 409
    assert "already tracked" in response.text
    assert len(db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()) == 1


def test_deleting_a_nonexistent_tracked_entity_returns_structured_404_not_500(client: TestClient):
    client_id = _create_client(client)

    response = client.post(f"/clients/{client_id}/tracked-entities/999999/delete", follow_redirects=False)

    assert response.status_code == 404
    assert response.json()["error_code"] == "tracked_entity_not_found"


def test_short_entity_name_is_not_blocked(client: TestClient, db_session: Session):
    """docs/TASKS_PHASE5.md design decision 8 — names under 4 chars get a UI warning, never a
    save-blocking error (e.g. "UAE" as a real-world tracked entity name).
    """
    client_id = _create_client(client)

    response = client.post(f"/clients/{client_id}/tracked-entities", data={"name": "UAE"}, follow_redirects=False)

    assert response.status_code == 303
    entities = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()
    assert len(entities) == 1
    assert entities[0].name == "UAE"


def test_add_and_delete_tracked_entity_alias(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()

    add_response = client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "VW"}, follow_redirects=False
    )
    assert add_response.status_code == 303

    aliases = db_session.scalars(
        select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)
    ).all()
    assert len(aliases) == 1
    assert aliases[0].alias == "VW"

    delete_response = client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases/{aliases[0].id}/delete", follow_redirects=False
    )
    assert delete_response.status_code == 303
    assert (
        db_session.scalars(select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)).all()
        == []
    )


def test_duplicate_tracked_entity_alias_returns_structured_conflict_not_500(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()
    client.post(f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "VW"}, follow_redirects=False)

    response = client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "vw"}, follow_redirects=False
    )

    assert response.status_code == 409
    assert (
        len(db_session.scalars(select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)).all())
        == 1
    )


def test_deleting_a_nonexistent_tracked_entity_alias_returns_structured_404_not_500(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()

    response = client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases/999999/delete", follow_redirects=False
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "tracked_entity_not_found"
