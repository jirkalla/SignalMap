"""Admin UI coverage for per-component model pricing (app/routers/ai_models.py, CC-3), written
here per docs/TASKS_COST_COMPONENTS.md CC-7: append-only writes, notes-only no-ops, single-
component version bumps, the "clearing an already-priced component is rejected" rule, and the
NUMERIC(12,6) precision regression the whole CC-1..CC-8 effort exists to fix (design decision 11).

`tests/test_ai_model_price_history.py` (the retiring flat cost_per_1k_*_usd columns) still covers
its own scenarios independently — this file is its component-table equivalent, not a replacement;
CC-8 retires the old file once the old columns themselves are dropped.
"""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AIModel, AIModelPriceComponent


def _components(db_session: Session, model_id: int) -> list[AIModelPriceComponent]:
    return (
        db_session.query(AIModelPriceComponent)
        .filter_by(ai_model_id=model_id)
        .order_by(AIModelPriceComponent.effective_from.desc())
        .all()
    )


def _create_model(authed_client: TestClient, provider_id: int, model_name: str, **prices: str) -> None:
    data = {
        "provider_id": provider_id,
        "model_name": model_name,
        "capability_tier": "standard",
        "price_input": "",
        "price_output": "",
        "price_cache_read": "",
        "price_cache_write": "",
        "price_cache_write_5m": "",
        "price_cache_write_1h": "",
    }
    data.update(prices)
    response = authed_client.post("/ai-models", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text


def test_create_model_with_prices_creates_rows_only_for_filled_fields(authed_client: TestClient, db_session: Session, seed: dict):
    _create_model(
        authed_client, seed["provider"].id, "priced-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.20",
    )

    model = db_session.query(AIModel).filter_by(model_name="priced-model").one()
    component_types = {c.component_type for c in _components(db_session, model.id)}

    assert component_types == {"input", "output", "cache_read"}


def test_editing_only_notes_adds_no_new_price_row(authed_client: TestClient, db_session: Session, seed: dict):
    _create_model(
        authed_client, seed["provider"].id, "notes-only-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.20",
    )
    model = db_session.query(AIModel).filter_by(model_name="notes-only-model").one()
    count_before = len(_components(db_session, model.id))

    response = authed_client.post(
        f"/ai-models/{model.id}/edit",
        data={
            "provider_id": seed["provider"].id,
            "model_name": "notes-only-model",
            "capability_tier": "standard",
            "price_input": "2.00",
            "price_output": "10.00",
            "price_cache_read": "0.20",
            "notes": "just a note update",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(_components(db_session, model.id)) == count_before


def test_changing_one_component_adds_a_row_only_for_it(authed_client: TestClient, db_session: Session, seed: dict):
    _create_model(
        authed_client, seed["provider"].id, "single-change-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.20",
    )
    model = db_session.query(AIModel).filter_by(model_name="single-change-model").one()
    before_by_type = {c.component_type: c.effective_from for c in _components(db_session, model.id)}

    response = authed_client.post(
        f"/ai-models/{model.id}/edit",
        data={
            "provider_id": seed["provider"].id,
            "model_name": "single-change-model",
            "capability_tier": "standard",
            "price_input": "2.00",
            "price_output": "10.00",
            "price_cache_read": "0.25",  # only this one actually changes
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    after = _components(db_session, model.id)
    after_by_type: dict[str, list[AIModelPriceComponent]] = {}
    for c in after:
        after_by_type.setdefault(c.component_type, []).append(c)

    assert len(after_by_type["cache_read"]) == 2  # new row for the changed component
    assert len(after_by_type["input"]) == 1  # unchanged components got no new row
    assert len(after_by_type["output"]) == 1
    assert after_by_type["input"][0].effective_from == before_by_type["input"]
    assert after_by_type["output"][0].effective_from == before_by_type["output"]


def test_clearing_a_filled_component_is_rejected(authed_client: TestClient, db_session: Session, seed: dict):
    _create_model(
        authed_client, seed["provider"].id, "clear-attempt-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.20",
    )
    model = db_session.query(AIModel).filter_by(model_name="clear-attempt-model").one()
    count_before = len(_components(db_session, model.id))
    cache_read_before = next(c for c in _components(db_session, model.id) if c.component_type == "cache_read")

    response = authed_client.post(
        f"/ai-models/{model.id}/edit",
        data={
            "provider_id": seed["provider"].id,
            "model_name": "clear-attempt-model",
            "capability_tier": "standard",
            "price_input": "2.00",
            "price_output": "10.00",
            "price_cache_read": "",  # attempt to clear an already-priced component
        },
    )

    assert response.status_code == 409
    after = _components(db_session, model.id)
    assert len(after) == count_before
    cache_read_after = next(c for c in after if c.component_type == "cache_read")
    assert cache_read_after.id == cache_read_before.id  # nothing new was written


def test_price_0_025_round_trips_without_precision_loss(authed_client: TestClient, db_session: Session, seed: dict):
    # Regression guard for design decision 11: the old NUMERIC(10,5) per-1k column would have
    # stored $0.025/1M as 0.000025/1k, ROUND_HALF_UP-ed to 0.00003 — a 20% error. The new
    # NUMERIC(12,6) per-1M column must hold the exact value.
    _create_model(
        authed_client, seed["provider"].id, "precision-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.025",
    )

    model = db_session.query(AIModel).filter_by(model_name="precision-model").one()
    cache_read = next(c for c in _components(db_session, model.id) if c.component_type == "cache_read")

    assert cache_read.price_per_unit_usd == Decimal("0.025000")
