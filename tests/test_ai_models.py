"""AIModel admin CRUD (app/routers/ai_models.py) and its delete/deactivate policy (P2-T3)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AIModel, Client, Prompt, PromptSet, Run


def _create_model(client: TestClient, provider_id: int, model_name: str = "test-model") -> None:
    response = client.post(
        "/ai-models",
        data={
            "provider_id": provider_id,
            "model_name": model_name,
            "display_name": "Test Model",
            "capability_tier": "standard",
            "cost_per_million_input_usd": "2.00",
            "cost_per_million_output_usd": "10.00",
            "context_window_tokens": "1000000",
            "max_output_tokens": "128000",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_create_list_edit_flow(client: TestClient, db_session: Session, seed: dict):
    _create_model(client, seed["anthropic_provider"].id, "test-model-1")

    list_response = client.get("/ai-models")
    assert list_response.status_code == 200
    assert "test-model-1" in list_response.text
    assert "$2.00 / $10.00 per 1M" in list_response.text

    model = db_session.query(AIModel).filter_by(model_name="test-model-1").one()

    edit_response = client.post(
        f"/ai-models/{model.id}/edit",
        data={
            "provider_id": seed["anthropic_provider"].id,
            "model_name": "test-model-1",
            "display_name": "Renamed Model",
            "capability_tier": "flagship",
            "cost_per_million_input_usd": "",
            "cost_per_million_output_usd": "",
            "context_window_tokens": "",
            "max_output_tokens": "",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert edit_response.status_code == 303
    assert "Renamed Model" in client.get("/ai-models").text


def test_duplicate_model_name_under_same_provider_is_rejected(client: TestClient, seed: dict):
    response = client.post(
        "/ai-models",
        data={
            "provider_id": seed["provider"].id,
            "model_name": seed["model"].model_name,
            "capability_tier": "standard",
        },
    )

    assert response.status_code == 409
    assert "already has a model with this exact model name" in response.text


def test_invalid_price_is_rejected(client: TestClient, seed: dict):
    response = client.post(
        "/ai-models",
        data={
            "provider_id": seed["provider"].id,
            "model_name": "bad-price-model",
            "capability_tier": "standard",
            "cost_per_million_input_usd": "not-a-number",
        },
    )

    assert response.status_code == 409
    assert "plain number" in response.text


def test_toggle_active_flips_state(client: TestClient, db_session: Session, seed: dict):
    model_id = seed["model"].id
    assert seed["model"].is_active is True

    client.post(f"/ai-models/{model_id}/toggle-active", follow_redirects=False)
    db_session.refresh(seed["model"])
    assert seed["model"].is_active is False

    client.post(f"/ai-models/{model_id}/toggle-active", follow_redirects=False)
    db_session.refresh(seed["model"])
    assert seed["model"].is_active is True


def test_delete_blocked_when_a_run_exists_against_the_model(client: TestClient, db_session: Session, seed: dict):
    # Runs always belong to a prompt (prompt_id NOT NULL) — build the minimal
    # chain, same as tests/test_clients.py's delete-block test.
    client_row = Client(name="Test Client", slug="test-client")
    db_session.add(client_row)
    db_session.flush()
    prompt_set = PromptSet(client_id=client_row.id, name="Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Q?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.flush()
    run = Run(prompt_id=prompt.id, model_id=seed["model"].id, market_id=seed["market"].id, status="success")
    db_session.add(run)
    db_session.commit()

    response = client.post(f"/ai-models/{seed['model'].id}/delete", follow_redirects=False)

    assert response.status_code == 409
    assert "1 run" in response.text
    assert db_session.get(AIModel, seed["model"].id) is not None


def test_delete_succeeds_for_a_model_with_no_runs(client: TestClient, db_session: Session, seed: dict):
    _create_model(client, seed["anthropic_provider"].id, "unused-model")
    model = db_session.query(AIModel).filter_by(model_name="unused-model").one()

    response = client.post(f"/ai-models/{model.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert db_session.get(AIModel, model.id) is None
