"""Tests for app/cli/backfill_sources.py (docs/TASKS_CITATION_VERIFICATION.md T6).

Runs/RawResponses/Citations built directly against db_session (same shape as
tests/test_verification_queue.py) — `run_backfill` is exercised directly, never `main()`
(argument parsing / process wiring isn't worth a subprocess test here, same reasoning as
scripts/create_admin.py).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.backfill_sources import run_backfill
from app.models import AIModel, Citation, Client, Prompt, PromptSet, RawResponse, Run
from app.models.verification import VerificationJob

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _make_raw_response(
    db_session: Session,
    seed: dict,
    prompt: Prompt,
    *,
    model: AIModel,
    created_at: datetime,
    citation_urls: tuple[str, ...] = (),
) -> RawResponse:
    run = Run(
        prompt_id=prompt.id,
        model_id=model.id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        trigger_type="manual",
        status="success",
    )
    db_session.add(run)
    db_session.flush()

    raw = RawResponse(
        run_id=run.id,
        raw_payload={"answer": "..."},
        rendered_text="Some rendered answer.",
        has_citations=bool(citation_urls),
        created_at=created_at,
    )
    db_session.add(raw)
    db_session.flush()

    for position, url in enumerate(citation_urls):
        db_session.add(
            Citation(raw_response_id=raw.id, source_url=url, source_domain="example.com", citation_position=position)
        )
    db_session.commit()
    db_session.refresh(raw)
    return raw


def _second_client_prompt(db_session: Session, seed: dict) -> Prompt:
    client = Client(name="Other Client", slug="other-client")
    db_session.add(client)
    db_session.flush()
    prompt_set = PromptSet(client_id=client.id, name="Other Set")
    db_session.add(prompt_set)
    db_session.flush()
    prompt = Prompt(prompt_set_id=prompt_set.id, text="Other prompt?", market_id=seed["market"].id)
    db_session.add(prompt)
    db_session.commit()
    db_session.refresh(prompt)
    return prompt


def _job_count(db_session: Session) -> int:
    return len(db_session.scalars(select(VerificationJob)).all())


# --- basic enqueue / dry-run --------------------------------------------------------------------


def test_dry_run_enqueues_nothing_but_reports_counts(db_session: Session, seed, sample_prompt: Prompt):
    _make_raw_response(
        db_session,
        seed,
        sample_prompt,
        model=seed["model"],
        created_at=NOW - timedelta(days=1),
        citation_urls=("https://a.example.com/x", "https://b.example.com/y"),
    )

    result = run_backfill(db_session, client_id=None, since=None, dry_run=True, now=NOW)

    assert result.response_count == 1
    assert result.citation_count == 2
    assert result.unique_url_count == 2
    assert result.enqueued is False
    assert _job_count(db_session) == 0


def test_real_run_enqueues_a_capture_job_per_eligible_response(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(
        db_session,
        seed,
        sample_prompt,
        model=seed["model"],
        created_at=NOW - timedelta(days=1),
        citation_urls=("https://a.example.com/x",),
    )

    result = run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW)

    assert result.response_count == 1
    assert result.enqueued is True
    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job is not None
    assert job.kind == "capture"
    assert job.status == "queued"


def test_responses_without_citations_are_skipped(db_session: Session, seed, sample_prompt: Prompt):
    _make_raw_response(db_session, seed, sample_prompt, model=seed["model"], created_at=NOW - timedelta(days=1))

    result = run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW)

    assert result.response_count == 0
    assert _job_count(db_session) == 0


# --- idempotence ----------------------------------------------------------------------------


def test_backfill_is_idempotent_across_two_runs(db_session: Session, seed, sample_prompt: Prompt):
    _make_raw_response(
        db_session,
        seed,
        sample_prompt,
        model=seed["model"],
        created_at=NOW - timedelta(days=1),
        citation_urls=("https://a.example.com/x",),
    )

    first = run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW)
    second = run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW + timedelta(minutes=1))

    assert first.response_count == 1
    assert second.response_count == 0  # already has a capture job — not re-enqueued
    assert _job_count(db_session) == 1


def test_backfill_skips_a_response_whose_capture_job_already_failed(db_session: Session, seed, sample_prompt: Prompt):
    """The idempotence guard checks for the EXISTENCE of a capture job, any status — a response

    whose earlier capture attempt ended in 'error' must not be silently retried by a plain
    re-run of this command (a deliberate retry is a separate, explicit action, not this one).
    """
    raw = _make_raw_response(
        db_session,
        seed,
        sample_prompt,
        model=seed["model"],
        created_at=NOW - timedelta(days=1),
        citation_urls=("https://a.example.com/x",),
    )
    db_session.add(VerificationJob(raw_response_id=raw.id, kind="capture", status="error"))
    db_session.commit()

    result = run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW)

    assert result.response_count == 0
    assert _job_count(db_session) == 1  # still just the one, pre-existing job


# --- filters ----------------------------------------------------------------------------------


def test_backfill_filters_by_client(db_session: Session, seed, sample_prompt: Prompt):
    other_prompt = _second_client_prompt(db_session, seed)
    _make_raw_response(
        db_session, seed, sample_prompt, model=seed["model"], created_at=NOW - timedelta(days=1),
        citation_urls=("https://a.example.com/x",),
    )
    _make_raw_response(
        db_session, seed, other_prompt, model=seed["model"], created_at=NOW - timedelta(days=1),
        citation_urls=("https://b.example.com/y",),
    )

    result = run_backfill(db_session, client_id=other_prompt.prompt_set.client_id, since=None, dry_run=True, now=NOW)

    assert result.response_count == 1
    assert result.unique_url_count == 1


def test_backfill_filters_by_since_date(db_session: Session, seed, sample_prompt: Prompt):
    _make_raw_response(
        db_session, seed, sample_prompt, model=seed["model"], created_at=NOW - timedelta(days=10),
        citation_urls=("https://old.example.com/x",),
    )
    _make_raw_response(
        db_session, seed, sample_prompt, model=seed["model"], created_at=NOW - timedelta(days=1),
        citation_urls=("https://new.example.com/y",),
    )

    result = run_backfill(db_session, client_id=None, since=NOW - timedelta(days=2), dry_run=True, now=NOW)

    assert result.response_count == 1
    assert result.unique_url_count == 1


# --- ordering -----------------------------------------------------------------------------------


def test_backfill_enqueues_gemini_responses_before_older_non_gemini_ones(
    db_session: Session, seed, sample_prompt: Prompt
):
    """Design decision 6/T6 point 1: Gemini redirects have no documented lifetime, so they must

    be enqueued first even when an older non-Gemini response is also waiting — "oldest first"
    is the tiebreaker WITHIN each group, not the overall sort key.
    """
    older_anthropic = _make_raw_response(
        db_session, seed, sample_prompt, model=seed["anthropic_model"], created_at=NOW - timedelta(days=10),
        citation_urls=("https://anthropic.example.com/x",),
    )
    newer_gemini = _make_raw_response(
        db_session, seed, sample_prompt, model=seed["model"], created_at=NOW - timedelta(days=1),
        citation_urls=("https://gemini.example.com/y",),
    )

    run_backfill(db_session, client_id=None, since=None, dry_run=False, now=NOW)

    gemini_job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == newer_gemini.id))
    anthropic_job = db_session.scalar(
        select(VerificationJob).where(VerificationJob.raw_response_id == older_anthropic.id)
    )
    assert gemini_job.id < anthropic_job.id
    assert gemini_job.scheduled_for < anthropic_job.scheduled_for
