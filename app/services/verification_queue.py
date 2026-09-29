"""The verification-job queue (docs/TASKS_CITATION_VERIFICATION.md T5) — enqueue-side and

claim-side operations over `verification_jobs`, the same `FOR UPDATE SKIP LOCKED` pattern
app/services/queue.py already uses for `run_queue`.

Runs always take priority (design decision 3): app/worker.py only ever calls `claim_next_job`
when `claim_next` (run_queue) returned nothing for that iteration. Nothing in this module
enforces that itself — it is purely a consequence of the order app/worker.py calls these
functions in, the same way app/services/queue.py's own functions don't know or care when
app/worker.py chooses to call them.
"""

import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.provider import AIModel, Provider
from app.models.run import Citation, RawResponse
from app.models.verification import VerificationJob
from app.services.citation_verification import verify_citations_by_quote
from app.services.claim_judge import DEFAULT_JUDGE_MODEL_NAME, DEFAULT_JUDGE_PROVIDER_CODE, LLM_JUDGE_PROVIDERS, judge_citations
from app.services.source_capture import capture_url

logger = logging.getLogger(__name__)

# T5 point 2 — 15 minutes: long enough that a job legitimately mid-capture (many citation URLs,
# each individually rate-limited to 1/s per domain) is never mistaken for abandoned, short
# enough that a worker that actually died doesn't leave the job stuck for long.
DEFAULT_LEASE_MINUTES = 15

# 1/5/25 minutes — the same backoff shape app/worker.py already uses for run_queue's transport
# retries, reused rather than invented fresh. Applies only to an UNEXPECTED exception escaping
# capture_url (a DB error, a bug) — capture_url itself never raises for an ordinary HTTP/robots/
# challenge outcome (403, 404, 5xx, a bot-challenge page, ...); those become
# `SourceDocument.error_reason`, which is evidence, not a job failure (see
# `process_verification_job`).
_BACKOFF_MINUTES = (1, 5, 25)
_MAX_ATTEMPTS = len(_BACKOFF_MINUTES)

# "limit času na úlohu" (T5 point 4) — one job can cover many citation URLs (a single Knauf run
# has had 37 citations); this caps how long ONE job may hold the worker, so an unusually
# source-heavy response can't starve run_queue for minutes. Each individual fetch already has
# its own cap (source_capture.REQUEST_TIMEOUT_SECONDS) — this is the job's OWN budget across
# however many of those it makes.
MAX_JOB_SECONDS = 60.0


def enqueue_capture(db: Session, raw_response_id: int, *, now: datetime) -> None:
    """Queue a 'capture' job for `raw_response_id` — best effort, same discipline as

    `_run_active_analysis_skills` (app/services/run_execution.py): the caller wraps this in its
    own try/except so a failure here can never fail the run itself. This only ever inserts a row
    (design decision 3's "insert into the queue only") — the actual fetch happens later, in the
    worker, never inline in a request or in `execute_run` itself.

    `now` is injected rather than read internally (`datetime.now(timezone.utc)`), matching every
    other queue/capture function in this branch (`capture_url`, `claim_next_job`, ...) — a
    `scheduled_for` set from the caller's own clock is what makes `claim_next_job`'s `<= now`
    comparison deterministic to test against.
    """
    db.add(
        VerificationJob(
            raw_response_id=raw_response_id,
            kind="capture",
            status="queued",
            priority=0,
            scheduled_for=now,
        )
    )
    db.commit()


def enqueue_judge(db: Session, raw_response_id: int, *, now: datetime, requested_by_user_id: int | None = None) -> None:
    """Queue a 'judge' job for `raw_response_id` (docs/TASKS_CITATION_VERIFICATION.md T13) — the

    explicit-trigger counterpart to `enqueue_capture` above: the automatic case (a client with
    `auto_verify_citations` on) never calls this at all, it rides along inside the capture job
    itself instead (see `process_verification_job`'s own docstring for why). This is only ever
    used for the two one-off human-triggered paths — the "Ověřit citace" button on a run and a
    client's retroactive bulk-verify — both of which set `requested_by_user_id`, unlike the
    automatic capture job's own `None` (`VerificationJob`'s docstring, T5).
    """
    db.add(
        VerificationJob(
            raw_response_id=raw_response_id,
            kind="judge",
            status="queued",
            priority=0,
            scheduled_for=now,
            requested_by_user_id=requested_by_user_id,
        )
    )
    db.commit()


def _default_judge_model(db: Session) -> AIModel:
    """The judge model design decision 20 names as the default — resolved by a fixed (provider,

    model_name) lookup, not a Settings field (see claim_judge.py's own constants for why). Raises
    clearly rather than silently doing nothing when it's missing: that only happens if a database
    was never seeded with this model row at all, a setup bug worth failing loudly on, not a
    routine "nothing to judge with" outcome to swallow.
    """
    model = db.scalar(
        select(AIModel)
        .join(Provider, AIModel.provider_id == Provider.id)
        .where(Provider.code == DEFAULT_JUDGE_PROVIDER_CODE, AIModel.model_name == DEFAULT_JUDGE_MODEL_NAME)
    )
    if model is None:
        raise RuntimeError(
            f"No ai_models row for provider={DEFAULT_JUDGE_PROVIDER_CODE!r} model_name={DEFAULT_JUDGE_MODEL_NAME!r} "
            "— the default citation-verification judge model (design decision 20) must be seeded."
        )
    return model


def claim_next_job(db: Session, *, worker_name: str, now: datetime, lease_minutes: int) -> VerificationJob | None:
    """Atomically take the next due verification job, or None — the same `FOR UPDATE SKIP LOCKED`

    idiom as `app/services/queue.py`'s `claim_next`, so two workers can never claim the same job
    twice. `status IN ('queued', 'deferred')` mirrors that function's own widening for the same
    reason: a job pushed back with a backoff (see `process_verification_job`) must eventually be
    reclaimed once its `scheduled_for` arrives.
    """
    job = db.scalars(
        select(VerificationJob)
        .where(VerificationJob.status.in_(("queued", "deferred")), VerificationJob.scheduled_for <= now)
        .order_by(VerificationJob.priority.desc(), VerificationJob.scheduled_for.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None

    job.status = "leased"
    job.leased_by = worker_name
    job.lease_expires_at = now + timedelta(minutes=lease_minutes)
    job.attempts += 1
    db.commit()
    return job


def release_expired_job_leases(db: Session, *, now: datetime) -> int:
    """Return a dead worker's claim to the queue — mirrors `app/services/queue.py`'s

    `release_expired_leases`. Unlike `run_queue` (design decision 13 there), there is no
    "might already have started something not safe to repeat" ambiguity to guard against: a
    `capture_url` call has no side effect worth not repeating (it's an idempotent-in-spirit
    fetch-and-record), so simply returning the lease to the queue is the whole reconciliation a
    stuck job needs — no `reconcile_interrupted_runs`-style counterpart required here.
    """
    stale = db.scalars(
        select(VerificationJob).where(VerificationJob.status == "leased", VerificationJob.lease_expires_at < now)
    ).all()
    for job in stale:
        job.status = "queued"
        job.leased_by = None
        job.lease_expires_at = None
    db.commit()
    return len(stale)


def _citation_urls(db: Session, raw_response_id: int) -> list[str]:
    """Every distinct, non-null citation source URL on one response, in citation_position order

    — stable and deterministic, so a job's behavior is reproducible to reason about and test,
    even though capture order has no effect on the result (each URL's own `capture_url` call is
    independent of every other).
    """
    urls = db.scalars(
        select(Citation.source_url)
        .where(Citation.raw_response_id == raw_response_id, Citation.source_url.isnot(None))
        .order_by(Citation.citation_position)
    ).all()
    seen: list[str] = []
    for url in urls:
        if url not in seen:
            seen.append(url)
    return seen


def _maybe_auto_judge(db: Session, raw_response: RawResponse, *, now: datetime) -> None:
    """Run the LLM paraphrase check inline, right after the capture job's own free quote check,

    when the response's client has `auto_verify_citations` on (docs/TASKS_CITATION_VERIFICATION.md
    T13, design decision 25) and its provider is one `judge_citations` can do anything with.

    Deliberately NOT a separately-enqueued 'judge' job: the worker claims queued jobs one at a
    time in priority/scheduled_for order (`claim_next_job`), with no guarantee a 'judge' job
    enqueued alongside a 'capture' job would be processed AFTER it — a 'judge' job claimed first
    would find no captured `SourceDocument` for any citation yet, run `judge_citations` to a
    no-op, and never automatically get retried once capture actually finishes (there is no
    "come back later" mechanism for a `done` job). Riding along inside the SAME already-in-
    progress capture job sidesteps that ordering race entirely — the same reasoning design
    decision 1 already gives for running T8's free quote check inline here instead of as its own
    job.
    """
    provider_code = raw_response.run.model.provider.code
    if provider_code not in LLM_JUDGE_PROVIDERS:
        return
    client_row = raw_response.run.prompt.prompt_set.client
    if not client_row.auto_verify_citations:
        return
    judge_citations(db, raw_response, judge_model=_default_judge_model(db), now=now)


def process_verification_job(db: Session, job: VerificationJob, *, now: datetime, client: httpx.Client) -> None:
    """Execute one already-`leased` verification job — 'capture' or 'judge' (docs/TASKS_CITATION_

    VERIFICATION.md T5/T13). A 'capture' job fetches every distinct citation URL on its response,
    runs the free literal-quote check (T8, design decision 1 — "the literal check is free and
    always runs" is what makes it safe to do inline here rather than queuing a separate job for
    it), and then `_maybe_auto_judge`s it (T13) if the client has opted in. A 'judge' job (T13)
    only ever comes from an explicit human trigger (the "Ověřit citace" button, or a client's
    retroactive bulk-verify) — see `enqueue_judge`'s own docstring for why the automatic case
    never creates one of these. Always ends this pass with exactly one outcome:

    - `done` — even when some or all individual URLs failed to capture (403, a bot challenge,
      whatever) or the job's time budget cut the list short. Those are recorded as evidence on
      their own `SourceDocument` rows; they are not reasons to fail the JOB.
    - `deferred` with backoff, or `error` once `_MAX_ATTEMPTS` is exhausted — only for an
      UNEXPECTED exception (a DB error, a bug), never for an ordinary capture outcome (see
      module docstring). `verify_citations_by_quote`'s archive.org fallback (T10, design
      decision 19) deliberately reuses this exact path: `archive_lookup.ArchiveUnavailable`
      (a 429/5xx/timeout/connection error from archive.org) is an ordinary `Exception` too, so a
      rate-limited archive.org defers-and-retries the whole job here, same as any other
      unexpected failure — never recorded as a verdict.
    """
    if job.kind not in ("capture", "judge"):
        raise NotImplementedError(f"verification job {job.id}: kind={job.kind!r} has no processor yet")

    # No "raw_response no longer exists" guard here: `raw_response_id` is `ondelete="CASCADE"`
    # (app/models/verification.py) — a deleted RawResponse takes its VerificationJob rows down
    # with it, so a job that's still claimable always still points at a real response.
    try:
        if job.kind == "capture":
            urls = _citation_urls(db, job.raw_response_id)
            started = time.monotonic()
            for index, url in enumerate(urls):
                capture_url(db, url, now=now, client=client)
                if time.monotonic() - started > MAX_JOB_SECONDS:
                    logger.info(
                        "verification job %s: hit its %ss budget after %d/%d URLs — finishing the rest on a later pass",
                        job.id,
                        MAX_JOB_SECONDS,
                        index + 1,
                        len(urls),
                    )
                    break

            raw_response = db.get(RawResponse, job.raw_response_id)
            verify_citations_by_quote(db, raw_response, now=now, client=client)
            _maybe_auto_judge(db, raw_response, now=now)
        else:
            raw_response = db.get(RawResponse, job.raw_response_id)
            judge_citations(db, raw_response, judge_model=_default_judge_model(db), now=now)
    except Exception as exc:  # noqa: BLE001 - classified below, not swallowed silently
        logger.error("verification job %s failed: %s", job.id, exc, exc_info=True, extra={"extra_data": {"job_id": job.id}})
        db.rollback()
        if job.attempts >= _MAX_ATTEMPTS:
            job.status = "error"
            job.error = str(exc)[:2000]
            job.finished_at = now
        else:
            job.status = "deferred"
            job.scheduled_for = now + timedelta(minutes=_BACKOFF_MINUTES[job.attempts - 1])
        db.commit()
        return

    job.status = "done"
    job.finished_at = now
    db.commit()
