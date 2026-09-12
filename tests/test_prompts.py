"""Prompt/prompt-set delete regression coverage.

Specifically covers the bug found in code review: deleting a run-less
multi-version prompt (or a prompt set containing one) used to 500 with
`ForeignKeyViolation` on `fk_prompts_root_prompt_id_prompts` — batching
same-table deletes without a flush between children and the root they
reference. Neither delete path had any test before this file.
"""

from fastapi.testclient import TestClient
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
