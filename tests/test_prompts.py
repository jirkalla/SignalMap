"""Prompt/prompt-set delete regression coverage, plus prompt-edit versioning behavior.

Delete coverage specifically covers the bug found in code review: deleting a run-less
multi-version prompt (or a prompt set containing one) used to 500 with
`ForeignKeyViolation` on `fk_prompts_root_prompt_id_prompts` — batching
same-table deletes without a flush between children and the root they
reference. Neither delete path had any test before this file.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Prompt, PromptSet


def _create_new_version(authed_client: TestClient, prompt_id: int, seed: dict, text: str) -> int:
    """Edit a prompt via the real endpoint (creates version+1) and return the new prompt id."""
    response = authed_client.post(
        f"/prompts/{prompt_id}/edit",
        data={"text": text, "market_id": seed["market"].id, "topic": "", "is_active": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_delete_prompt_lineage_with_multiple_versions_and_no_runs_succeeds(
    authed_client: TestClient, db_session: Session, seed: dict, sample_prompt: Prompt
):
    v2_id = _create_new_version(authed_client, sample_prompt.id, seed, "Second version of the question?")
    v3_id = _create_new_version(authed_client, v2_id, seed, "Third version of the question?")

    response = authed_client.post(f"/prompts/{v3_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    for prompt_id in (sample_prompt.id, v2_id, v3_id):
        assert db_session.get(Prompt, prompt_id) is None


def test_delete_prompt_set_with_versioned_prompt_and_no_runs_succeeds(
    authed_client: TestClient, db_session: Session, seed: dict, sample_prompt: Prompt
):
    v2_id = _create_new_version(authed_client, sample_prompt.id, seed, "Edited question?")
    prompt_set_id = sample_prompt.prompt_set_id

    response = authed_client.post(f"/prompt-sets/{prompt_set_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert db_session.get(PromptSet, prompt_set_id) is None
    assert db_session.get(Prompt, sample_prompt.id) is None
    assert db_session.get(Prompt, v2_id) is None


def test_toggling_active_alone_does_not_create_a_new_version(
    authed_client: TestClient, db_session: Session, seed: dict, sample_prompt: Prompt
):
    """Found in conversation, 2026-09-15 — a real prompt ended up 4 versions deep from two
    is_active toggles with identical text, because `update_prompt` used to version every save
    unconditionally. `Prompt`'s own docstring already calls `is_active` "orthogonal to whether
    it's the current version"; the route now actually honors that when nothing else changed.
    """
    response = authed_client.post(
        f"/prompts/{sample_prompt.id}/edit",
        data={
            "text": sample_prompt.text,
            "market_id": seed["market"].id,
            "topic": "",
            "is_active": "false",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/prompts/{sample_prompt.id}"  # same id — no new row

    db_session.refresh(sample_prompt)
    assert sample_prompt.version == 1
    assert sample_prompt.is_current_version is True
    assert sample_prompt.is_active is False

    lineage = db_session.scalars(
        select(Prompt).where((Prompt.id == sample_prompt.id) | (Prompt.root_prompt_id == sample_prompt.id))
    ).all()
    assert len(lineage) == 1  # no second row was ever created


def test_editing_text_still_creates_a_new_version(
    authed_client: TestClient, db_session: Session, seed: dict, sample_prompt: Prompt
):
    """Regression guard for the fix above — a real content change must still version as before."""
    v2_id = _create_new_version(authed_client, sample_prompt.id, seed, "A genuinely different question?")

    assert v2_id != sample_prompt.id
    db_session.refresh(sample_prompt)
    assert sample_prompt.is_current_version is False

    v2 = db_session.get(Prompt, v2_id)
    assert v2.version == 2
    assert v2.is_current_version is True
    assert v2.text == "A genuinely different question?"
