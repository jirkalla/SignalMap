"""The scheduler worker process (docs/TASKS_SCHEDULER.md T3) — run as its own Compose service

(`python -m app.worker`, T4) from the exact same image as the web app, never as part of it: the
ticker and executor both need a long-lived process, which a request/response web server isn't.

Loop, once per iteration:
  1. Heartbeat (DB `worker_heartbeats` row + `/tmp/worker-alive` touchfile, design decision 19) —
     without this, a stopped worker and an empty queue look identical on `/schedules`.
  2. Ticker (`enqueue_due_schedules`) — at most once a minute, skipped entirely when
     `SCHEDULER_ENABLED=false` (design decision 16); the executor below keeps draining the
     queue regardless, since two app instances must never both plan on their own schedule.
  3. Lease/Run reconciliation (`release_expired_leases`, `reconcile_interrupted_runs`) — also
     at most once a minute.
  3b. Notification checks (`check_expiring_schedules`, `check_budget_thresholds`, T8) — same
     once-a-minute cadence; both are pure state observation (nothing about them ties to a single
     queue item), so they ride the ticker interval rather than getting a schedule of their own.
  4. `claim_next` + `process_claimed_item` — at most one item per iteration; a terminal failure
     here also fires a `schedule.run_failed` notification (T8), only on the FINAL attempt, never
     on a transport retry that might still recover.
  5. Sleep 5 seconds.

`process_claimed_item` is the one piece worth unit testing directly (tests/test_worker_queue.py)
— everything else here is process/IO plumbing around it and app/services/queue.py's functions.
"""

import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models.client import Client
from app.models.market import Market
from app.models.notification import WorkerHeartbeat
from app.models.persona import Persona
from app.models.prompt import Prompt
from app.models.provider import AIModel
from app.models.run import Run
from app.models.schedule import RunQueueItem
from app.services.notifications import check_budget_thresholds, check_expiring_schedules, notify
from app.services.queue import claim_next, enqueue_due_schedules, reconcile_interrupted_runs, release_expired_leases
from app.services.run_execution import build_request_payload, execute_run

logger = logging.getLogger(__name__)

# Matches app/main.py's FastAPI `version=` — no shared constant exists yet for either to import;
# duplicated rather than introducing a cross-import between an entrypoint module and another.
APP_VERSION = "0.1.0"

HEARTBEAT_FILE = Path("/tmp/worker-alive")
_TICKER_INTERVAL = timedelta(minutes=1)
_ITEM_POLL_INTERVAL_SECONDS = 5

# 1 / 5 / 25 minutes (docs/TASKS_SCHEDULER.md T3) — shared by two different reasons an item goes
# back to the queue: a collision with a still-running Run for the same prompt+model (design
# decision 17, never terminal, capped at the last step forever) and a retryable transport/429
# provider failure (terminal once `attempts` reaches len(this list), i.e. 3 tries).
_BACKOFF_MINUTES = (1, 5, 25)
_MAX_TRANSPORT_ATTEMPTS = len(_BACKOFF_MINUTES)


def _backoff_minutes(attempts: int) -> int:
    """1/5/25 minutes for attempts 1/2/3+ (`attempts` is 1-indexed — `claim_next` increments it
    before this is ever consulted, so an item on its first attempt always has `attempts == 1`).
    """
    index = max(attempts - 1, 0)
    return _BACKOFF_MINUTES[min(index, len(_BACKOFF_MINUTES) - 1)]


def _is_retryable_error(exc: Exception) -> bool:
    """Whether `exc` (as raised by a provider adapter, app/adapters/*.py) is worth retrying.

    Duck-typed on `status_code`/`code` rather than importing each provider SDK's exception
    classes directly — a worker reaching into `anthropic.RateLimitError` etc. would be exactly
    the kind of provider-specific coupling the adapter layer exists to prevent (app/adapters/
    base.py). All three SDKs (google-genai, anthropic, openai) attach one of these two attribute
    names to a real API error response; an exception with NEITHER never got a response at all —
    a DNS failure, connection refused, or timeout — which is transient by definition.

    429 (rate limited) and 5xx (provider-side failure) are retryable; any other HTTP status
    (400, 401, 404, ...) reflects something wrong with the request itself, which retrying would
    only repeat identically — that is the "terminal" case docs/TASKS_SCHEDULER.md T3 describes
    as "a chyba, kterou provider vrátil po odpovědi".
    """
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "code", None)
    if isinstance(status_code, int):
        return status_code == 429 or status_code >= 500
    return True


def process_claimed_item(
    db: Session,
    item: RunQueueItem,
    *,
    now: datetime,
    dry_run: bool,
    grace_period_minutes: int,
) -> None:
    """Execute (or skip) one already-`leased` queue item — the executor half of the worker.

    Every branch below ends the item's processing for this pass by committing exactly one
    outcome: `skipped` (grace expired, inactive prompt/model, or dry-run), `deferred` (a
    same-prompt-and-model Run is already `pending`, design decision 17), or a real attempt at
    running it, which itself ends in `done` or `queued`/`error` depending on retryability.
    """
    # Design decision 11b: the grace check also applies here, not just at enqueue time — a
    # worker that fell behind must give up on a stale item exactly like one that was down when
    # it should have been enqueued, and for the same two reasons (unplanned cost, wrong-dated
    # evidence) design decision 11 already gives for skipping it at enqueue time instead.
    if now - item.scheduled_for > timedelta(minutes=grace_period_minutes):
        item.status = "skipped"
        item.skip_reason = "grace_expired"
        item.finished_at = now
        db.commit()
        return

    prompt = db.get(Prompt, item.prompt_id)
    model = db.get(AIModel, item.model_id)
    if not prompt.is_active:
        item.status, item.skip_reason = "skipped", "inactive_prompt"
        item.finished_at = now
        db.commit()
        return
    if not model.is_active:
        item.status, item.skip_reason = "skipped", "inactive_model"
        item.finished_at = now
        db.commit()
        return

    # Design decision 17 — mirrors the manual-trigger guard in app/routers/runs.py's
    # trigger_run, but the outcome here is "come back later", never a 409: nothing asked this
    # item to run right now the way a human clicking a button did, so postponing it is free.
    collision = db.scalar(
        select(Run.id)
        .where(Run.prompt_id == item.prompt_id, Run.model_id == item.model_id, Run.status == "pending")
        .limit(1)
    )
    if collision is not None:
        item.status = "deferred"
        item.scheduled_for = now + timedelta(minutes=_backoff_minutes(item.attempts))
        db.commit()
        return

    if dry_run:
        item.status, item.skip_reason = "skipped", "dry_run"
        item.finished_at = now
        db.commit()
        return

    market = db.get(Market, item.market_id)
    persona = db.get(Persona, item.persona_id)

    # Design decision 13: the Run is created and its id committed onto this queue row BEFORE
    # execute_run ever calls the adapter — a worker killed between here and the adapter
    # returning leaves a Run this queue item already points at, for reconcile_interrupted_runs
    # to resolve, instead of a queue item with no trace of what (if anything) was actually sent.
    run = Run(
        prompt_id=prompt.id,
        model_id=model.id,
        market_id=market.id,
        persona_id=persona.id,
        trigger_type="scheduled",
        status="pending",
        request_payload=build_request_payload(db, prompt=prompt, model=model, market=market, persona=persona),
        triggered_by_user_id=None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    item.run_id = run.id
    db.commit()

    try:
        execute_run(
            db,
            prompt=prompt,
            model=model,
            market=market,
            persona=persona,
            trigger_type="scheduled",
            triggered_by_user_id=None,
            run_id=run.id,
            reraise_on_failure=True,
        )
    except Exception as exc:  # noqa: BLE001 - classified below, not swallowed silently
        if _is_retryable_error(exc) and item.attempts < _MAX_TRANSPORT_ATTEMPTS:
            item.status = "queued"
            item.scheduled_for = now + timedelta(minutes=_backoff_minutes(item.attempts))
            db.commit()
        else:
            item.status = "error"
            item.last_error = str(exc)
            item.finished_at = now
            db.commit()
            # Only on the FINAL failure, never on a transport retry above — T8 design decision
            # 25's whole point is a notification means "this needs a human", not "a retry queued
            # itself", so a flaky 429 that recovers on attempt 2 never reaches here.
            client = db.get(Client, item.client_id)
            notify(
                db,
                "schedule.run_failed",
                {
                    "queue_item_id": item.id,
                    "schedule_id": item.schedule_id,
                    "client_id": item.client_id,
                    "client_name": client.name if client is not None else None,
                    "run_id": run.id,
                    "prompt_text": prompt.text[:200],
                    "error": str(exc)[:500],
                },
            )
        return

    item.status = "done"
    item.finished_at = now
    db.commit()


def write_heartbeat(db: Session, *, worker_name: str, dry_run: bool, now: datetime) -> None:
    """Upsert this worker's liveness row and touch the healthcheck file (design decision 19)."""
    stmt = (
        pg_insert(WorkerHeartbeat)
        .values(worker_name=worker_name, last_seen_at=now, version=APP_VERSION, dry_run=dry_run)
        .on_conflict_do_update(
            index_elements=["worker_name"],
            set_={"last_seen_at": now, "version": APP_VERSION, "dry_run": dry_run},
        )
    )
    db.execute(stmt)
    db.commit()
    HEARTBEAT_FILE.write_text(now.isoformat())


def run_forever() -> None:
    """The worker's main loop — see module docstring for the per-iteration sequence."""
    configure_logging()
    settings = get_settings()

    stop_requested = False

    def _handle_sigterm(signum, frame) -> None:  # noqa: ANN001 - stdlib signal handler signature
        nonlocal stop_requested
        logger.info("Worker %s received signal %s, finishing current item before exit", settings.worker_name, signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    last_ticker_run = datetime.min.replace(tzinfo=timezone.utc)

    logger.info(
        "Worker %s starting (dry_run=%s, enabled=%s)",
        settings.worker_name,
        settings.scheduler_dry_run,
        settings.scheduler_enabled,
    )

    with SessionLocal() as db:
        reconcile_interrupted_runs(db, now=datetime.now(timezone.utc))

    while not stop_requested:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            write_heartbeat(db, worker_name=settings.worker_name, dry_run=settings.scheduler_dry_run, now=now)

            if now - last_ticker_run >= _TICKER_INTERVAL:
                if settings.scheduler_enabled:
                    enqueue_due_schedules(db, now=now, grace_period_minutes=settings.scheduler_grace_period_minutes)
                release_expired_leases(db, now=now)
                reconcile_interrupted_runs(db, now=now)
                check_expiring_schedules(db, now=now)
                check_budget_thresholds(db, now=now)
                last_ticker_run = now

            item = claim_next(db, worker_name=settings.worker_name, now=now, lease_minutes=settings.scheduler_lease_minutes)
            if item is not None:
                process_claimed_item(
                    db,
                    item,
                    now=now,
                    dry_run=settings.scheduler_dry_run,
                    grace_period_minutes=settings.scheduler_grace_period_minutes,
                )

        if not stop_requested:
            time.sleep(_ITEM_POLL_INTERVAL_SECONDS)

    logger.info("Worker %s stopped", settings.worker_name)


if __name__ == "__main__":
    run_forever()
