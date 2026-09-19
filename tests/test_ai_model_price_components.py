"""Admin UI coverage for per-component model pricing (app/routers/ai_models.py, CC-3), written
here per docs/TASKS_COST_COMPONENTS.md CC-7: append-only writes, notes-only no-ops, single-
component version bumps, the "clearing an already-priced component is rejected" rule, and the
NUMERIC(12,6) precision regression the whole CC-1..CC-8 effort exists to fix (design decision 11).

`tests/test_ai_model_price_history.py` (the retiring flat cost_per_1k_*_usd columns) still covers
its own scenarios independently — this file is its component-table equivalent, not a replacement;
CC-8 retires the old file once the old columns themselves are dropped.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AIModel, AIModelPriceComponent
from app.routers.ai_models import _price_history_rows


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


# --- Price history display: grouped per component, adjacent equal prices coalesced --------------


def _edit_model(authed_client: TestClient, model: AIModel, provider_id: int, **prices: str):
    data = {
        "provider_id": provider_id,
        "model_name": model.model_name,
        "capability_tier": "standard",
    }
    data.update(prices)
    return authed_client.post(f"/ai-models/{model.id}/edit", data=data, follow_redirects=False)


def _model(db_session: Session, name: str) -> AIModel:
    return db_session.query(AIModel).filter_by(model_name=name).one()


def test_two_records_at_the_same_price_show_as_one_continuous_span(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """Two records, one span.

    The rows are written directly rather than through the form: the edit route only writes a row
    when the submitted price differs from the one in effect, so this shape cannot be produced by
    ordinary editing. It exists in production anyway — docs/TASKS_PRE_SCHEDULER.md PRE-0 backfilled
    earlier validity for prices that were already current, by hand, in SQL. Displayed one row per
    record, that draws a boundary at the moment the price was RECORDED, where the price itself
    never changed, and a reader stops to look for a change that isn't there (reported from the edit
    page, 2026-09-19).
    """
    _create_model(authed_client, seed["provider"].id, "history-model", price_input="2.00")
    model = _model(db_session, "history-model")
    backdated = (datetime.now(timezone.utc) - timedelta(days=10)).replace(microsecond=0)
    db_session.add(
        AIModelPriceComponent(
            ai_model_id=model.id, component_type="input",
            price_per_unit_usd=Decimal("2.00"), effective_from=backdated,
        )
    )
    db_session.commit()
    db_session.refresh(model)

    assert len(_components(db_session, model.id)) == 2, "both records are kept; only the display merges"

    spans = dict(_price_history_rows(model))["input"]
    assert len(spans) == 1, "one price, one span — no boundary where nothing changed"
    assert spans[0].valid_from == backdated, "the span reaches back to the earlier record"
    assert spans[0].valid_until is None, "and is still in effect"
    assert spans[0].price_per_unit_usd == Decimal("2.000000")


def test_a_real_price_change_is_not_merged_away(authed_client: TestClient, db_session: Session, seed: dict):
    """The other half of the same rule: coalescing must not hide a change that did happen."""
    _create_model(authed_client, seed["provider"].id, "changed-model", price_input="2.00")
    model = _model(db_session, "changed-model")
    _edit_model(authed_client, model, seed["provider"].id, price_input="3.00")
    db_session.refresh(model)

    spans = dict(_price_history_rows(model))["input"]
    assert [s.price_per_unit_usd for s in spans] == [Decimal("3.000000"), Decimal("2.000000")]
    assert spans[0].valid_until is None
    assert spans[1].valid_until == spans[0].valid_from, "the older span ends where the newer starts"


def test_a_price_that_returns_to_an_earlier_value_keeps_both_spans(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """2.00 -> 3.00 -> 2.00 is three spans, not two. Only ADJACENT equal prices merge; a price that
    goes away and comes back is two separate stretches and has to read as such.
    """
    _create_model(authed_client, seed["provider"].id, "roundtrip-model", price_input="2.00")
    model = _model(db_session, "roundtrip-model")
    _edit_model(authed_client, model, seed["provider"].id, price_input="3.00")
    _edit_model(authed_client, model, seed["provider"].id, price_input="2.00")
    db_session.refresh(model)

    spans = dict(_price_history_rows(model))["input"]
    assert [s.price_per_unit_usd for s in spans] == [
        Decimal("2.000000"), Decimal("3.000000"), Decimal("2.000000")
    ]


def test_history_is_grouped_per_component_so_each_has_its_own_current(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """Three components priced at once means three spans reading "current" — one per component,
    which is correct and is exactly what a single date-ordered list made unreadable.
    """
    _create_model(
        authed_client, seed["provider"].id, "grouped-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.025",
    )
    model = _model(db_session, "grouped-model")
    _edit_model(
        authed_client, model, seed["provider"].id,
        price_input="2.00", price_output="10.00", price_cache_read="0.030",  # only cache_read moves
    )
    db_session.refresh(model)

    groups = _price_history_rows(model)
    assert [component_type for component_type, _ in groups] == ["input", "output", "cache_read"], (
        "groups follow COMPONENT_TYPES order, not the order rows happen to come back in"
    )

    by_type = dict(groups)
    assert len(by_type["input"]) == 1 and len(by_type["output"]) == 1
    assert len(by_type["cache_read"]) == 2, "the component that actually changed keeps both spans"
    assert all(spans[0].valid_until is None for spans in by_type.values()), (
        "every component's newest span is current — three at once is correct, not a conflict"
    )


def test_components_that_never_changed_are_not_given_a_table(
    authed_client: TestClient, db_session: Session, seed: dict
):
    """An Anthropic model prices five components and typically changes two of them ever, so a table
    per component would put three header rows above three single values. Those render as one line
    each instead; only a component with more than one span earns a table.
    """
    _create_model(
        authed_client, seed["provider"].id, "five-component-model",
        price_input="2.00", price_output="10.00", price_cache_read="0.10",
        price_cache_write_5m="1.25", price_cache_write_1h="2.00",
    )
    model = _model(db_session, "five-component-model")
    _edit_model(
        authed_client, model, seed["provider"].id,
        price_input="3.00", price_output="10.00", price_cache_read="0.10",
        price_cache_write_5m="1.25", price_cache_write_1h="2.00",
    )
    db_session.refresh(model)

    by_type = dict(_price_history_rows(model))
    assert len(by_type["input"]) == 2, "the one component that changed gets a table"
    assert [len(by_type[ct]) for ct in ("output", "cache_read", "cache_write_5m", "cache_write_1h")] == [1, 1, 1, 1]

    page = authed_client.get(f"/ai-models/{model.id}/edit")
    assert page.status_code == 200
    # The price fields above are a grid, not a table, so the only <table> on the page is the
    # history of the one component that actually changed.
    assert page.text.count("<table") == 1, "the four unchanged components must not each get a table"
    assert "Unchanged since entered" in page.text, "they are listed as single lines instead"
    assert "$0.1 " in page.text and "$1.25 " in page.text, "with their price on that line"
