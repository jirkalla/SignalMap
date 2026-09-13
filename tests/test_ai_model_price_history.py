"""AIModel price history (app/routers/ai_models.py, CPH-T1) — a new row only on an actual price change."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AIModel, AIModelPriceHistory


def _edit_model(authed_client: TestClient, model: AIModel, *, cost_in: str, cost_out: str, notes: str = "") -> None:
    response = authed_client.post(
        f"/ai-models/{model.id}/edit",
        data={
            "provider_id": model.provider_id,
            "model_name": model.model_name,
            "display_name": model.display_name or "",
            "capability_tier": model.capability_tier,
            "cost_per_million_input_usd": cost_in,
            "cost_per_million_output_usd": cost_out,
            "context_window_tokens": "",
            "max_output_tokens": "",
            "notes": notes,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_creating_a_model_writes_its_first_price_history_row(authed_client: TestClient, db_session: Session, seed: dict):
    response = authed_client.post(
        "/ai-models",
        data={
            "provider_id": seed["provider"].id,
            "model_name": "history-test-model",
            "capability_tier": "standard",
            "cost_per_million_input_usd": "2.00",
            "cost_per_million_output_usd": "10.00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    model = db_session.query(AIModel).filter_by(model_name="history-test-model").one()
    history = db_session.query(AIModelPriceHistory).filter_by(ai_model_id=model.id).all()

    assert len(history) == 1
    assert history[0].cost_per_1k_input_usd == model.cost_per_1k_input_usd
    assert history[0].cost_per_1k_output_usd == model.cost_per_1k_output_usd


def test_editing_with_a_changed_price_adds_a_history_row(authed_client: TestClient, db_session: Session, seed: dict):
    model = seed["model"]
    initial_count = len(db_session.query(AIModelPriceHistory).filter_by(ai_model_id=model.id).all())

    _edit_model(authed_client, model, cost_in="3.00", cost_out="15.00")

    history = (
        db_session.query(AIModelPriceHistory)
        .filter_by(ai_model_id=model.id)
        .order_by(AIModelPriceHistory.effective_from)
        .all()
    )
    assert len(history) == initial_count + 1
    db_session.refresh(model)
    assert history[-1].cost_per_1k_input_usd == model.cost_per_1k_input_usd
    assert history[-1].cost_per_1k_output_usd == model.cost_per_1k_output_usd


def test_editing_without_a_price_change_adds_no_history_row(authed_client: TestClient, db_session: Session, seed: dict):
    model = seed["model"]
    _edit_model(authed_client, model, cost_in="3.00", cost_out="15.00", notes="first change")
    count_after_first_change = len(db_session.query(AIModelPriceHistory).filter_by(ai_model_id=model.id).all())

    # Same price, only notes differ — must not add another row.
    _edit_model(authed_client, model, cost_in="3.00", cost_out="15.00", notes="just a note update")

    count_after_second_edit = len(db_session.query(AIModelPriceHistory).filter_by(ai_model_id=model.id).all())
    assert count_after_second_edit == count_after_first_change
