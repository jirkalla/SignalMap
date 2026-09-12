"""Market format validation and its delete policy (app/routers/markets.py)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Client, Market, Prompt, PromptSet


def test_invalid_language_code_is_rejected(authed_client: TestClient):
    response = authed_client.post(
        "/markets", data={"code": "xx-ZZ", "language": "English", "country": "", "locale_name": ""}
    )

    assert response.status_code == 409
    assert "ISO 639-1" in response.text


def test_invalid_country_code_is_rejected(authed_client: TestClient):
    response = authed_client.post(
        "/markets", data={"code": "xx-ZZ", "language": "en", "country": "USA", "locale_name": ""}
    )

    assert response.status_code == 409
    assert "ISO 3166-1" in response.text


def test_delete_blocked_when_a_prompt_uses_the_market(authed_client: TestClient, db_session: Session):
    market = Market(code="fr-FR", language="fr", country="FR", locale_name="French (France)")
    db_session.add(market)
    db_session.flush()
    client_row = Client(name="Test Client", slug="test-authed_client")
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name="Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Q?", market_id=market.id)
    db_session.add(prompt)
    db_session.commit()

    response = authed_client.post(f"/markets/{market.id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert "1 prompt" in response.text
    assert db_session.get(Market, market.id) is not None
