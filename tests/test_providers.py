"""Provider list + edit-name (app/routers/providers.py) — read + edit-name only, no create/delete."""

from fastapi.testclient import TestClient


def test_list_shows_every_provider(admin_client: TestClient, seed: dict):
    response = admin_client.get("/providers")

    assert response.status_code == 200
    assert seed["provider"].name in response.text
    assert seed["anthropic_provider"].name in response.text


def test_edit_name_is_saved(admin_client: TestClient, seed: dict):
    response = admin_client.post(
        f"/providers/{seed['anthropic_provider'].id}", data={"name": "Anthropic (Claude)"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert "Anthropic (Claude)" in admin_client.get("/providers").text


def test_empty_name_is_rejected(admin_client: TestClient, seed: dict):
    response = admin_client.post(f"/providers/{seed['provider'].id}", data={"name": "   "})

    assert response.status_code == 400
    assert seed["provider"].name in response.text
