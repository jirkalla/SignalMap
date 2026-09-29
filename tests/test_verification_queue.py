"""Tests for app/services/verification_queue.py (docs/TASKS_CITATION_VERIFICATION.md T5).

Runs/RawResponses/Citations are built directly against db_session (same "build the row, call
the pure/DB function, assert on the row" shape as tests/test_worker_queue.py) — no real network,
`process_verification_job` is always given an httpx.Client backed by httpx.MockTransport.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Citation, Prompt, RawResponse, Run
from app.models.verification import SourceDocument, VerificationJob
from app.services.verification_queue import (
    DEFAULT_LEASE_MINUTES,
    _MAX_ATTEMPTS,
    claim_next_job,
    enqueue_capture,
    process_verification_job,
    release_expired_job_leases,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _make_raw_response(db_session: Session, seed: dict, sample_prompt: Prompt, *, citation_urls: list[str]) -> RawResponse:
    run = Run(
        prompt_id=sample_prompt.id,
        model_id=seed["model"].id,
        market_id=seed["market"].id,
        persona_id=seed["persona"].id,
        trigger_type="manual",
        status="success",
    )
    db_session.add(run)
    db_session.flush()

    raw = RawResponse(run_id=run.id, raw_payload={"answer": "..."}, rendered_text="Some rendered answer.")
    db_session.add(raw)
    db_session.flush()

    for position, url in enumerate(citation_urls):
        db_session.add(
            Citation(raw_response_id=raw.id, source_url=url, source_domain="example.com", citation_position=position)
        )
    db_session.commit()
    db_session.refresh(raw)
    return raw


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _allow_all_200(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(200, content=b"<html><body><p>Content.</p></body></html>", headers={"content-type": "text/html"})


# --- enqueue_capture --------------------------------------------------------------------------


def test_enqueue_capture_creates_a_queued_capture_job(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])

    enqueue_capture(db_session, raw.id, now=NOW)

    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job is not None
    assert job.kind == "capture"
    assert job.status == "queued"
    assert job.attempts == 0


# --- claim_next_job ---------------------------------------------------------------------------


def test_claim_next_job_returns_none_when_queue_is_empty(db_session: Session):
    assert claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES) is None


def test_claim_next_job_leases_and_increments_attempts(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)

    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)

    assert job is not None
    assert job.status == "leased"
    assert job.leased_by == "w1"
    assert job.lease_expires_at == NOW + timedelta(minutes=DEFAULT_LEASE_MINUTES)
    assert job.attempts == 1
    # Already leased — a second claim must find nothing due right now.
    assert claim_next_job(db_session, worker_name="w2", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES) is None


def test_claim_next_job_ignores_jobs_not_yet_due(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)
    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    job.status = "deferred"
    job.scheduled_for = NOW + timedelta(minutes=5)
    db_session.commit()

    assert claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES) is None
    claimed = claim_next_job(
        db_session, worker_name="w1", now=NOW + timedelta(minutes=6), lease_minutes=DEFAULT_LEASE_MINUTES
    )
    assert claimed is not None


# --- release_expired_job_leases ----------------------------------------------------------------


def test_release_expired_job_leases_requeues_stale_lease(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)
    claim_next_job(db_session, worker_name="dead-worker", now=NOW, lease_minutes=15)

    released = release_expired_job_leases(db_session, now=NOW + timedelta(minutes=16))

    assert released == 1
    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job.status == "queued"
    assert job.leased_by is None
    assert job.lease_expires_at is None


def test_release_expired_job_leases_leaves_fresh_lease_alone(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)
    claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=15)

    released = release_expired_job_leases(db_session, now=NOW + timedelta(minutes=1))

    assert released == 0


# --- process_verification_job ------------------------------------------------------------------


def test_process_verification_job_captures_every_unique_citation_url(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(
        db_session,
        seed,
        sample_prompt,
        citation_urls=["https://a.example.com/x", "https://b.example.com/y", "https://a.example.com/x"],
    )
    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)

    process_verification_job(db_session, job, now=NOW, client=_client(_allow_all_200))

    assert job.status == "done"
    assert job.finished_at == NOW
    documents = db_session.scalars(select(SourceDocument)).all()
    # Two DISTINCT URLs cited (the third citation repeats the first) — one SourceDocument each,
    # not three, and not one merged/skipped one either.
    assert {d.requested_url for d in documents} == {"https://a.example.com/x", "https://b.example.com/y"}
    assert len(documents) == 2


def test_process_verification_job_with_no_citations_still_completes(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=[])
    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)

    process_verification_job(db_session, job, now=NOW, client=_client(_allow_all_200))

    assert job.status == "done"
    assert db_session.scalars(select(SourceDocument)).all() == []


def test_deleting_the_raw_response_cascades_to_its_verification_jobs(db_session: Session, seed, sample_prompt: Prompt):
    """Confirms the schema guarantee process_verification_job's own docstring relies on instead

    of a defensive "raw_response no longer exists" check: raw_response_id is ondelete='CASCADE'
    (app/models/verification.py), so a job can never outlive the response it points at.
    """
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)

    db_session.delete(raw)
    db_session.commit()

    assert db_session.scalars(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id)).all() == []


def test_process_verification_job_defers_with_backoff_on_unexpected_exception(
    db_session: Session, seed, sample_prompt: Prompt
):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)
    assert job.attempts == 1

    def broken_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("boom")

    process_verification_job(db_session, job, now=NOW, client=_client(broken_handler))

    assert job.status == "deferred"
    assert job.scheduled_for == NOW + timedelta(minutes=1)  # first backoff step
    assert job.finished_at is None


def test_process_verification_job_becomes_error_after_max_attempts(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])
    enqueue_capture(db_session, raw.id, now=NOW)
    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    job.status = "leased"
    job.leased_by = "w1"
    job.attempts = _MAX_ATTEMPTS
    db_session.commit()

    def broken_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("boom")

    process_verification_job(db_session, job, now=NOW, client=_client(broken_handler))

    assert job.status == "error"
    assert job.finished_at == NOW


def test_process_verification_job_wrong_kind_raises_not_implemented(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=[])
    job = VerificationJob(raw_response_id=raw.id, kind="judge", status="leased", leased_by="w1")
    db_session.add(job)
    db_session.commit()

    with pytest.raises(NotImplementedError):
        process_verification_job(db_session, job, now=NOW, client=_client(_allow_all_200))
