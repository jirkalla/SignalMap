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

from app.adapters.base import JudgePayload
from app.models import AIModel, Citation, Prompt, RawResponse, Run, User
from app.models.verification import CitationVerification, SourceDocument, SourceText, VerificationJob
from app.services.claim_judge import DEFAULT_JUDGE_MODEL_NAME
from app.services.source_capture import build_capture_client
from app.services.verification_queue import (
    DEFAULT_LEASE_MINUTES,
    _MAX_ATTEMPTS,
    _default_judge_model,
    claim_next_job,
    enqueue_capture,
    enqueue_judge,
    process_verification_job,
    release_expired_job_leases,
)
from tests.fake_adapter import FakeAdapter

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _seed_default_judge_model(db_session: Session, seed: dict) -> AIModel:
    """The `(provider, model_name)` pair `_default_judge_model` looks up — `seed["anthropic_model"]`

    is named `claude-test-model`, not the real default, so any test exercising auto-judge or a
    'judge' job needs this seeded explicitly first.
    """
    model = AIModel(
        provider_id=seed["anthropic_provider"].id, model_name=DEFAULT_JUDGE_MODEL_NAME,
        capability_tier="economy", supports_web_search=False, is_active=True,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _make_judgeable_raw_response(db_session: Session, seed: dict, sample_prompt: Prompt) -> tuple[RawResponse, Citation, str]:
    """A google_gemini-provider run with one citation shaped for `derive_claim` (offsets into

    `rendered_text`, same pattern as tests/test_claim_judge.py) — what `_maybe_auto_judge`/a
    'judge' job actually needs to have anything to check, unlike this file's own `_make_raw_response`
    above, whose bare `source_url`-only citations are enough for capture but not for judging.
    """
    claim_text = "Acme is known for reliability."
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id,
        persona_id=seed["persona"].id, trigger_type="manual", status="success",
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={"answer": claim_text}, rendered_text=claim_text, has_citations=True)
    db_session.add(raw)
    db_session.flush()
    citation = Citation(
        raw_response_id=raw.id, source_url="https://example.com/a", source_domain="example.com",
        citation_position=0, cited_answer_span=claim_text, answer_span_start=0, answer_span_end=len(claim_text),
    )
    db_session.add(citation)
    db_session.commit()
    db_session.refresh(raw)
    db_session.refresh(citation)
    return raw, citation, claim_text


def _valid_judge_reply(claim_text: str) -> JudgePayload:
    return JudgePayload(
        text=f'{{"verdict": "supported", "reason": "The page states this directly.", "quote": "{claim_text}"}}',
        token_usage={"input_tokens": 50, "output_tokens": 5},
    )


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
    return build_capture_client(transport=httpx.MockTransport(handler))


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
    """'capture' and 'judge' are the only two kinds the CHECK constraint (migration 0036) allows

    — this constructs an in-memory-only job with a made-up third kind (never `db.add()`/
    `db.commit()`ed, so the constraint is never actually exercised) purely to confirm
    `process_verification_job`'s own guard still fails loudly if a kind ever existed that no
    branch here handles, rather than silently doing nothing.
    """
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=[])
    job = VerificationJob(raw_response_id=raw.id, kind="reprocess", status="leased", leased_by="w1")

    with pytest.raises(NotImplementedError):
        process_verification_job(db_session, job, now=NOW, client=_client(_allow_all_200))


# --- enqueue_judge / auto-judge / 'judge' jobs (docs/TASKS_CITATION_VERIFICATION.md T13) --------


def test_enqueue_judge_creates_a_queued_judge_job_with_requested_by(db_session: Session, seed, sample_prompt: Prompt, admin_user: User):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])

    enqueue_judge(db_session, raw.id, now=NOW, requested_by_user_id=admin_user.id)

    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job.kind == "judge"
    assert job.status == "queued"
    assert job.requested_by_user_id == admin_user.id


def test_enqueue_judge_defaults_requested_by_to_none_for_the_automatic_case(db_session: Session, seed, sample_prompt: Prompt):
    raw = _make_raw_response(db_session, seed, sample_prompt, citation_urls=["https://example.com/a"])

    enqueue_judge(db_session, raw.id, now=NOW)

    job = db_session.scalar(select(VerificationJob).where(VerificationJob.raw_response_id == raw.id))
    assert job.requested_by_user_id is None


def test_default_judge_model_raises_clearly_when_not_seeded(db_session: Session, seed, sample_prompt: Prompt):
    """`seed["anthropic_model"]` is named "claude-test-model", not the real default — this fixture

    deliberately never seeds the real one, so `_default_judge_model` must fail loudly rather than
    silently return nothing usable.
    """
    with pytest.raises(RuntimeError):
        _default_judge_model(db_session)


def test_process_capture_job_auto_judges_when_the_client_has_opted_in(db_session: Session, seed, sample_prompt: Prompt):
    _seed_default_judge_model(db_session, seed)
    sample_prompt.prompt_set.client.auto_verify_citations = True
    db_session.commit()
    raw, citation, claim_text = _make_judgeable_raw_response(db_session, seed, sample_prompt)
    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)
    FakeAdapter.judge_payload_to_return = _valid_judge_reply(claim_text)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=f"<html><body><p>{claim_text}</p></body></html>".encode("utf-8"), headers={"content-type": "text/html"})

    process_verification_job(db_session, job, now=NOW, client=_client(handler))

    assert job.status == "done"
    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification is not None
    assert verification.check_type == "llm"
    assert verification.verdict == "llm_supported"


def test_process_capture_job_does_not_auto_judge_when_the_client_has_not_opted_in(db_session: Session, seed, sample_prompt: Prompt):
    # Deliberately does NOT seed the default judge model either — if this ever tried to judge
    # anyway, it would fail loudly (RuntimeError) rather than silently, so a passing "done" status
    # here is itself part of the proof that _maybe_auto_judge never even attempted it.
    raw, citation, claim_text = _make_judgeable_raw_response(db_session, seed, sample_prompt)
    assert sample_prompt.prompt_set.client.auto_verify_citations is False
    enqueue_capture(db_session, raw.id, now=NOW)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=f"<html><body><p>{claim_text}</p></body></html>".encode("utf-8"), headers={"content-type": "text/html"})

    process_verification_job(db_session, job, now=NOW, client=_client(handler))

    assert job.status == "done"
    assert db_session.scalars(select(CitationVerification).where(CitationVerification.citation_id == citation.id)).all() == []


def test_process_judge_job_calls_judge_citations_with_the_default_model(db_session: Session, seed, sample_prompt: Prompt, admin_user: User):
    import hashlib

    _seed_default_judge_model(db_session, seed)
    raw, citation, claim_text = _make_judgeable_raw_response(db_session, seed, sample_prompt)
    # Already captured directly (no live fetch needed for a 'judge' job — capture is a 'capture'
    # job's own responsibility, exercised separately above).
    source_text = f"On our page: {claim_text} Read more."
    text_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    db_session.add(SourceText(sha256=text_sha256, text=source_text, chars=len(source_text)))
    db_session.add(
        SourceDocument(
            requested_url=citation.source_url, method="live", http_status=200, text_sha256=text_sha256,
            fetched_at=NOW, verifier_version="1.0",
        )
    )
    db_session.commit()

    enqueue_judge(db_session, raw.id, now=NOW, requested_by_user_id=admin_user.id)
    job = claim_next_job(db_session, worker_name="w1", now=NOW, lease_minutes=DEFAULT_LEASE_MINUTES)
    FakeAdapter.judge_payload_to_return = _valid_judge_reply(claim_text)

    process_verification_job(db_session, job, now=NOW, client=_client(_allow_all_200))

    assert job.status == "done"
    verification = db_session.scalar(select(CitationVerification).where(CitationVerification.citation_id == citation.id))
    assert verification is not None
    assert verification.verdict == "llm_supported"
    assert verification.llm_quote_found is True
