"""Backfill capture jobs for historical raw responses that predate T5's automatic enqueue

(docs/TASKS_CITATION_VERIFICATION.md T6). Every day of delay means more of these citation
sources have quietly changed or disappeared (design decision 14) — this closes that gap for
responses that already happened, not just runs going forward.

Only ENQUEUES capture jobs (app.services.verification_queue.enqueue_capture) — the actual
fetching still happens exclusively in the worker (app/worker.py), at the same per-domain pace
`capture_url` already enforces (design decision 9). This command never downloads anything itself.

Idempotent: a response that already has a capture job — regardless of that job's own status
(queued, done, error, ...) — is skipped, so running this command twice, or on a schedule, never
double-enqueues.

Order: Gemini responses first (oldest to newest), then everything else (oldest to newest) —
Gemini's grounding-redirect links have no documented lifetime (design decision 6), so they are
the most time-sensitive to capture before they might stop resolving.

Run from inside the app container, as a module (see scripts/create_admin.py for why):

    docker compose exec app python -m app.cli.backfill_sources [--client ID] [--since YYYY-MM-DD] [--dry-run]

`--dry-run` prints the same counts a real run would, but enqueues nothing.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AIModel, Citation, Prompt, PromptSet, Provider, RawResponse, Run
from app.models.verification import VerificationJob
from app.services.verification_queue import enqueue_capture


def _candidates(db: Session, *, client_id: int | None, since: datetime | None) -> list[tuple[int, bool]]:
    """(raw_response_id, is_gemini) for every raw response that has citations, has no capture

    job yet (the idempotence guard — any status counts, not just a finished one), and matches
    the optional --client/--since filters. Ordered Gemini-first, oldest-to-newest within each
    group — see module docstring.
    """
    already_has_capture_job = exists().where(
        VerificationJob.raw_response_id == RawResponse.id, VerificationJob.kind == "capture"
    )
    is_gemini = Provider.code == "google_gemini"
    query = (
        select(RawResponse.id, is_gemini)
        .join(Run, Run.id == RawResponse.run_id)
        .join(AIModel, AIModel.id == Run.model_id)
        .join(Provider, Provider.id == AIModel.provider_id)
        .join(Prompt, Prompt.id == Run.prompt_id)
        .join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
        .where(RawResponse.has_citations.is_(True), ~already_has_capture_job)
    )
    if client_id is not None:
        query = query.where(PromptSet.client_id == client_id)
    if since is not None:
        query = query.where(RawResponse.created_at >= since)
    query = query.order_by(is_gemini.desc(), RawResponse.created_at.asc())
    return [(row[0], row[1]) for row in db.execute(query).all()]


def _citation_counts(db: Session, raw_response_ids: list[int]) -> tuple[int, int]:
    """(citation_count, unique_url_count) across the given raw responses, for the summary line

    both --dry-run and a real run print.
    """
    if not raw_response_ids:
        return 0, 0
    urls = db.scalars(
        select(Citation.source_url).where(
            Citation.raw_response_id.in_(raw_response_ids), Citation.source_url.isnot(None)
        )
    ).all()
    return len(urls), len(set(urls))


@dataclass(frozen=True)
class BackfillResult:
    response_count: int
    gemini_count: int
    citation_count: int
    unique_url_count: int
    enqueued: bool


def run_backfill(
    db: Session, *, client_id: int | None, since: datetime | None, dry_run: bool, now: datetime
) -> BackfillResult:
    """The whole command, minus argument parsing and printing — the piece worth testing directly

    (tests/test_backfill_sources.py), same reasoning as `app/services/queue.py`'s
    `enqueue_due_schedules` being separated from `app/worker.py`'s loop around it. `now` is
    injected (never read internally), same convention every capture/queue function in this
    branch follows.
    """
    candidates = _candidates(db, client_id=client_id, since=since)
    raw_response_ids = [raw_response_id for raw_response_id, _ in candidates]
    gemini_count = sum(1 for _, is_gemini in candidates if is_gemini)
    citation_count, unique_url_count = _citation_counts(db, raw_response_ids)

    if not dry_run:
        # Each row gets a distinct, strictly increasing `scheduled_for` (never the same `now`
        # for the whole batch) — claim_next_job's own ordering is `priority DESC, scheduled_for
        # ASC`, so identical timestamps across thousands of backfilled rows would make its
        # Gemini-first/oldest-first guarantee depend on Postgres's unspecified tie-break order
        # instead of the order this loop actually enqueued them in.
        for index, raw_response_id in enumerate(raw_response_ids):
            enqueue_capture(db, raw_response_id, now=now + timedelta(microseconds=index))

    return BackfillResult(
        response_count=len(raw_response_ids),
        gemini_count=gemini_count,
        citation_count=citation_count,
        unique_url_count=unique_url_count,
        enqueued=not dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, dest="client_id", help="Only backfill this client's runs.")
    parser.add_argument("--since", type=str, help="Only backfill responses on/after this date (YYYY-MM-DD).")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only — enqueue nothing.")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    db = SessionLocal()
    try:
        result = run_backfill(db, client_id=args.client_id, since=since, dry_run=args.dry_run, now=datetime.now(timezone.utc))
        print(f"{result.response_count} response(s) without a capture job ({result.gemini_count} Gemini)")
        print(f"{result.citation_count} citation(s), {result.unique_url_count} unique URL(s)")
        if args.dry_run:
            print("--dry-run: nothing enqueued.")
        else:
            print(f"Enqueued {result.response_count} capture job(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
