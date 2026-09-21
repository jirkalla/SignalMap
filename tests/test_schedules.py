"""Tests for app/routers/schedules.py's find_overlapping_schedules (docs/TASKS_SCHEDULER.md T5c,
design decision 36) — warn-never-block detection of two independent active schedules that would
run the same prompt on the same model for the same persona.

Uses RunSchedule instances built directly against db_session, same "build the row, call the pure/
DB function, assert on the result" shape as tests/test_worker_queue.py — the CRUD routes
themselves (T5/T5b) have no route-level tests yet either, only browser verification.
"""

from datetime import date, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import AIModel, Client, Persona, Prompt, PromptSet, User
from app.models.schedule import RunSchedule
from app.routers.schedules import find_overlapping_schedules

TODAY = date(2026, 9, 21)


def _make_schedule(
    db_session: Session,
    *,
    client: Client,
    target_type: str,
    target_id: int,
    model_ids: list[int],
    persona_ids: list[int],
    created_by: User,
    **overrides,
) -> RunSchedule:
    defaults = dict(
        client_id=client.id,
        target_type=target_type,
        target_id=target_id,
        model_ids=model_ids,
        market_id=None,
        persona_ids=persona_ids,
        frequency="daily",
        time_of_day=time(6, 0),
        timezone="Europe/Prague",
        starts_on=TODAY,
        ends_on=TODAY + timedelta(days=30),
        priority=100,
        is_active=True,
        created_by_user_id=created_by.id,
    )
    defaults.update(overrides)
    schedule = RunSchedule(**defaults)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


@pytest.fixture
def second_persona(db_session: Session) -> Persona:
    persona = Persona(label="second persona")
    db_session.add(persona)
    db_session.commit()
    db_session.refresh(persona)
    return persona


@pytest.fixture
def second_prompt(db_session: Session, sample_prompt: Prompt, seed: dict) -> Prompt:
    """A second prompt in sample_prompt's own prompt set, so a prompt_set-level schedule's

    resolved target genuinely spans more than one prompt (design decision 32).
    """
    prompt = Prompt(
        prompt_set_id=sample_prompt.prompt_set_id,
        text="A second prompt in the same set",
        market_id=seed["market"].id,
    )
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return prompt


def test_detects_overlap_on_full_match(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    existing = _make_schedule(
        db_session,
        client=client,
        target_type="prompt",
        target_id=sample_prompt.id,
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
        created_by=admin_user,
    )

    overlaps = find_overlapping_schedules(
        db_session,
        client_id=client.id,
        exclude_schedule_id=None,
        prompt_ids=[sample_prompt.id],
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
    )

    assert len(overlaps) == 1
    assert overlaps[0].schedule.id == existing.id
    assert overlaps[0].shared_prompt_count == 1
    assert overlaps[0].shared_model_labels
    assert overlaps[0].shared_persona_labels


def test_no_overlap_on_partial_match(db_session, seed, sample_prompt, admin_user, second_persona):
    client = sample_prompt.prompt_set.client
    _make_schedule(
        db_session,
        client=client,
        target_type="prompt",
        target_id=sample_prompt.id,
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
        created_by=admin_user,
    )

    # Same prompt and model, but a different persona — a match on only two of the three
    # dimensions is not an overlap (design decision 36).
    overlaps = find_overlapping_schedules(
        db_session,
        client_id=client.id,
        exclude_schedule_id=None,
        prompt_ids=[sample_prompt.id],
        model_ids=[seed["model"].id],
        persona_ids=[second_persona.id],
    )

    assert overlaps == []


def test_no_overlap_with_itself_during_edit(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    existing = _make_schedule(
        db_session,
        client=client,
        target_type="prompt",
        target_id=sample_prompt.id,
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
        created_by=admin_user,
    )

    overlaps = find_overlapping_schedules(
        db_session,
        client_id=client.id,
        exclude_schedule_id=existing.id,
        prompt_ids=[sample_prompt.id],
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
    )

    assert overlaps == []


def test_no_overlap_with_paused_schedule(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    _make_schedule(
        db_session,
        client=client,
        target_type="prompt",
        target_id=sample_prompt.id,
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
        created_by=admin_user,
        is_active=False,
        inactive_reason="user",
    )

    overlaps = find_overlapping_schedules(
        db_session,
        client_id=client.id,
        exclude_schedule_id=None,
        prompt_ids=[sample_prompt.id],
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
    )

    assert overlaps == []


def test_detects_overlap_between_prompt_and_prompt_set_schedules(
    db_session, seed, sample_prompt, second_prompt, admin_user
):
    """The exact scenario that motivated T5c: an active prompt-set schedule already covers a

    prompt that a new prompt-level schedule (or vice versa) is about to target too, on the same
    model/persona.
    """
    client = sample_prompt.prompt_set.client
    existing = _make_schedule(
        db_session,
        client=client,
        target_type="prompt_set",
        target_id=sample_prompt.prompt_set_id,
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
        created_by=admin_user,
    )

    overlaps = find_overlapping_schedules(
        db_session,
        client_id=client.id,
        exclude_schedule_id=None,
        prompt_ids=[sample_prompt.id],
        model_ids=[seed["model"].id],
        persona_ids=[seed["persona"].id],
    )

    assert len(overlaps) == 1
    assert overlaps[0].schedule.id == existing.id
    assert overlaps[0].shared_prompt_count == 1
