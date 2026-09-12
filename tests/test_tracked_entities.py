"""Tracked entity (competitor) and their alias management (docs/TASKS_PHASE5.md P5-T4) — data
feeding the competitive_visibility analysis skill's matching.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TrackedEntity, TrackedEntityAlias


def _create_client(authed_client: TestClient, name: str = "Acme Corporation") -> int:
    response = authed_client.post(
        "/clients",
        data={"name": name, "industry": "Automotive", "notes": "", "domain": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_add_and_delete_tracked_entity(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)

    add_response = authed_client.post(
        f"/clients/{client_id}/tracked-entities",
        data={"name": "Volkswagen", "domain": "vw.com"},
        follow_redirects=False,
    )
    assert add_response.status_code == 303

    entities = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()
    assert len(entities) == 1
    assert entities[0].name == "Volkswagen"
    assert entities[0].domain == "vw.com"

    delete_response = authed_client.post(
        f"/clients/{client_id}/tracked-entities/{entities[0].id}/delete", follow_redirects=False
    )
    assert delete_response.status_code == 303
    assert db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all() == []


def test_tracked_entity_domain_is_optional(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)

    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()
    assert entity.domain is None


def test_duplicate_tracked_entity_name_returns_structured_conflict_not_500(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)

    response = authed_client.post(
        f"/clients/{client_id}/tracked-entities", data={"name": "volkswagen"}, follow_redirects=False
    )

    assert response.status_code == 409
    assert "already tracked" in response.text
    assert len(db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()) == 1


def test_deleting_a_nonexistent_tracked_entity_returns_404_not_500(authed_client: TestClient):
    """A plain form POST (not /api/*) gets the rendered HTML error page (app/errors.py's
    content-negotiation), not raw structured JSON — same status code either way, just not a 500.
    """
    client_id = _create_client(authed_client)

    response = authed_client.post(f"/clients/{client_id}/tracked-entities/999999/delete", follow_redirects=False)

    assert response.status_code == 404
    assert "Tracked entity not found" in response.text


def test_short_entity_name_is_not_blocked(authed_client: TestClient, db_session: Session):
    """docs/TASKS_PHASE5.md design decision 8 — names under 4 chars get a UI warning, never a
    save-blocking error (e.g. "UAE" as a real-world tracked entity name).
    """
    client_id = _create_client(authed_client)

    response = authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "UAE"}, follow_redirects=False)

    assert response.status_code == 303
    entities = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).all()
    assert len(entities) == 1
    assert entities[0].name == "UAE"


def test_add_and_delete_tracked_entity_alias(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()

    add_response = authed_client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "VW"}, follow_redirects=False
    )
    assert add_response.status_code == 303

    aliases = db_session.scalars(
        select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)
    ).all()
    assert len(aliases) == 1
    assert aliases[0].alias == "VW"

    delete_response = authed_client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases/{aliases[0].id}/delete", follow_redirects=False
    )
    assert delete_response.status_code == 303
    assert (
        db_session.scalars(select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)).all()
        == []
    )


def test_duplicate_tracked_entity_alias_returns_structured_conflict_not_500(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()
    authed_client.post(f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "VW"}, follow_redirects=False)

    response = authed_client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases", data={"alias": "vw"}, follow_redirects=False
    )

    assert response.status_code == 409
    assert (
        len(db_session.scalars(select(TrackedEntityAlias).where(TrackedEntityAlias.tracked_entity_id == entity.id)).all())
        == 1
    )


def test_deleting_a_nonexistent_tracked_entity_alias_returns_404_not_500(authed_client: TestClient, db_session: Session):
    client_id = _create_client(authed_client)
    authed_client.post(f"/clients/{client_id}/tracked-entities", data={"name": "Volkswagen"}, follow_redirects=False)
    entity = db_session.scalars(select(TrackedEntity).where(TrackedEntity.client_id == client_id)).first()

    response = authed_client.post(
        f"/clients/{client_id}/tracked-entities/{entity.id}/aliases/999999/delete", follow_redirects=False
    )

    assert response.status_code == 404
    assert "Tracked entity not found" in response.text
