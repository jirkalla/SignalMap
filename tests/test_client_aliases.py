"""Client domain field and alias management (docs/TASKS_PHASE3.md P3-T2) — data feeding
the mention_visibility analysis skill's matching.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, ClientAlias


def _create_client(client: TestClient, name: str = "Acme Corporation", domain: str = "") -> int:
    response = client.post(
        "/clients",
        data={"name": name, "industry": "Automotive", "notes": "", "domain": domain},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_domain_is_saved_and_lowercased_through_create_and_edit(client: TestClient, db_session: Session):
    client_id = _create_client(client, domain="Acme.COM")

    row = db_session.get(Client, client_id)
    assert row.domain == "acme.com"

    edit_response = client.post(
        f"/clients/{client_id}/edit",
        data={"name": "Acme Corporation", "industry": "Automotive", "notes": "", "domain": "New-Domain.com"},
        follow_redirects=False,
    )
    assert edit_response.status_code == 303
    db_session.refresh(row)
    assert row.domain == "new-domain.com"


def test_domain_left_blank_is_stored_as_none(client: TestClient, db_session: Session):
    client_id = _create_client(client)

    assert db_session.get(Client, client_id).domain is None


def test_add_and_delete_alias(client: TestClient, db_session: Session):
    client_id = _create_client(client)

    add_response = client.post(f"/clients/{client_id}/aliases", data={"alias": "Acme"}, follow_redirects=False)
    assert add_response.status_code == 303

    aliases = db_session.scalars(select(ClientAlias).where(ClientAlias.client_id == client_id)).all()
    assert len(aliases) == 1
    assert aliases[0].alias == "Acme"

    delete_response = client.post(
        f"/clients/{client_id}/aliases/{aliases[0].id}/delete", follow_redirects=False
    )
    assert delete_response.status_code == 303
    assert db_session.scalars(select(ClientAlias).where(ClientAlias.client_id == client_id)).all() == []


def test_duplicate_alias_returns_structured_conflict_not_500(client: TestClient, db_session: Session):
    client_id = _create_client(client)
    client.post(f"/clients/{client_id}/aliases", data={"alias": "Acme"}, follow_redirects=False)

    response = client.post(f"/clients/{client_id}/aliases", data={"alias": "acme"}, follow_redirects=False)

    assert response.status_code == 409
    assert "already registered" in response.text
    assert len(db_session.scalars(select(ClientAlias).where(ClientAlias.client_id == client_id)).all()) == 1


def test_deleting_a_nonexistent_alias_returns_structured_404_not_500(client: TestClient):
    client_id = _create_client(client)

    response = client.post(f"/clients/{client_id}/aliases/999999/delete", follow_redirects=False)

    assert response.status_code == 404
    assert response.json()["error_code"] == "client_alias_not_found"
