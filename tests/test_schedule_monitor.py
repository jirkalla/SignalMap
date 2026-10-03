"""Tests for app/services/schedule_monitor.py (docs/TASKS_SCHEDULER.md T6) — worker liveness,
the active queue, and batched history behind the /schedules monitoring page.

Same "build the row directly against db_session, call the pure/DB function, assert on the
result" shape as tests/test_worker_queue.py — no route-level tests here either (T5/T5b/T6 have
none yet, only browser verification), just the aggregation layer.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.i18n import get_translator
from app.models import AIModel, Client, Persona, Prompt, RawResponse, Run, VerificationJob
from app.models.notification import WorkerHeartbeat
from app.models.schedule import RunQueueItem
from app.services.schedule_monitor import (
    active_queue_rows,
    format_duration_short,
    history_batches,
    oldest_queued_age_seconds,
    queue_summary,
    schedule_health,
    skip_reason_labels,
    skipped_summary,
    worker_statuses,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
T = get_translator("en")


def _make_queue_item(db_session: Session, *, prompt: Prompt, client: Client, model: AIModel, persona: Persona, **overrides) -> RunQueueItem:
    defaults = dict(
        schedule_id=None,
        source="schedule",
        client_id=client.id,
        prompt_id=prompt.id,
        model_id=model.id,
        market_id=prompt.market_id,
        persona_id=persona.id,
        scheduled_for=NOW,
        queued_at=NOW,
        priority=100,
        status="queued",
    )
    defaults.update(overrides)
    item = RunQueueItem(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.fixture
def second_prompt(db_session: Session, sample_prompt: Prompt, seed: dict) -> Prompt:
    """A second prompt in sample_prompt's own prompt set, for search tests that need to tell two

    items' prompt text apart within the same client.
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


def test_worker_statuses_stale_threshold(db_session):
    db_session.add_all(
        [
            WorkerHeartbeat(worker_name="fresh", last_seen_at=NOW - timedelta(seconds=30), dry_run=False),
            WorkerHeartbeat(worker_name="dead", last_seen_at=NOW - timedelta(minutes=5), dry_run=False),
        ]
    )
    db_session.commit()

    statuses = {s.worker_name: s for s in worker_statuses(db_session, now=NOW)}

    assert statuses["fresh"].is_stale is False
    assert statuses["dead"].is_stale is True


def _leased_item(db_session, sample_prompt, seed, *, worker: str, claimed_at: datetime, lease_minutes: int = 15, **overrides):
    client = sample_prompt.prompt_set.client
    return _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        status="leased", leased_by=worker, leased_until=claimed_at + timedelta(minutes=lease_minutes), **overrides,
    )


def test_worker_statuses_show_the_leased_run_each_worker_is_processing(db_session, seed, sample_prompt):
    db_session.add_all(
        [
            WorkerHeartbeat(worker_name="worker-1", last_seen_at=NOW, dry_run=False),
            WorkerHeartbeat(worker_name="worker-2", last_seen_at=NOW, dry_run=False),
        ]
    )
    db_session.commit()
    _leased_item(db_session, sample_prompt, seed, worker="worker-1", claimed_at=NOW - timedelta(seconds=40))

    statuses = {s.worker_name: s for s in worker_statuses(db_session, now=NOW)}

    work = statuses["worker-1"].current_run
    assert work.kind == "run"
    assert work.client_name == sample_prompt.prompt_set.client.name
    assert work.seconds == 40  # age of the CURRENT claim (leased_until - lease), not started_at
    assert work.is_long is False
    assert statuses["worker-1"].current_job is None
    assert statuses["worker-2"].current_work is None  # idle


def test_worker_statuses_since_ignores_started_at_of_an_earlier_attempt(db_session, seed, sample_prompt):
    db_session.add(WorkerHeartbeat(worker_name="worker-1", last_seen_at=NOW, dry_run=False))
    db_session.commit()
    _leased_item(
        db_session, sample_prompt, seed, worker="worker-1", claimed_at=NOW - timedelta(seconds=10),
        started_at=NOW - timedelta(hours=2),
    )

    work = worker_statuses(db_session, now=NOW)[0].current_run

    assert work.seconds == 10


def test_worker_statuses_flag_a_long_call(db_session, seed, sample_prompt):
    db_session.add(WorkerHeartbeat(worker_name="worker-1", last_seen_at=NOW, dry_run=False))
    db_session.commit()
    _leased_item(db_session, sample_prompt, seed, worker="worker-1", claimed_at=NOW - timedelta(seconds=300))

    assert worker_statuses(db_session, now=NOW, long_call_seconds=240)[0].current_run.is_long is True
    assert worker_statuses(db_session, now=NOW, long_call_seconds=600)[0].current_run.is_long is False


def test_worker_statuses_show_a_leased_verification_job(db_session, seed, sample_prompt):
    run = Run(
        prompt_id=sample_prompt.id, model_id=seed["model"].id, market_id=seed["market"].id,
        persona_id=seed["persona"].id, trigger_type="manual", status="success",
    )
    db_session.add(run)
    db_session.flush()
    raw = RawResponse(run_id=run.id, raw_payload={}, rendered_text="x")
    db_session.add(raw)
    db_session.flush()
    db_session.add_all(
        [
            WorkerHeartbeat(worker_name="worker-3", last_seen_at=NOW, dry_run=False),
            VerificationJob(
                raw_response_id=raw.id, kind="capture", status="leased", leased_by="worker-3",
                lease_expires_at=NOW + timedelta(minutes=15) - timedelta(seconds=90),
            ),
        ]
    )
    db_session.commit()

    status = worker_statuses(db_session, now=NOW)[0]

    assert status.current_run is None
    assert status.current_job.kind == "capture"
    assert status.current_job.run_id == run.id
    assert status.current_job.seconds == 90


def test_worker_statuses_are_sorted_by_name(db_session):
    db_session.add_all(
        [WorkerHeartbeat(worker_name=n, last_seen_at=NOW, dry_run=False) for n in ("worker-3", "worker-1", "worker-2")]
    )
    db_session.commit()

    assert [s.worker_name for s in worker_statuses(db_session, now=NOW)] == ["worker-1", "worker-2", "worker-3"]


def test_workers_fragment_route_renders_the_panel(admin_client, db_session, seed, sample_prompt):
    db_session.add_all(
        [
            WorkerHeartbeat(worker_name="worker-1", last_seen_at=datetime.now(timezone.utc), dry_run=False),
            WorkerHeartbeat(worker_name="worker-2", last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=5), dry_run=False),
        ]
    )
    db_session.commit()
    claimed = datetime.now(timezone.utc) - timedelta(seconds=30)
    _leased_item(db_session, sample_prompt, seed, worker="worker-1", claimed_at=claimed)

    response = admin_client.get("/schedules/workers")

    assert response.status_code == 200
    assert 'id="workers-live"' in response.text
    assert "worker-1" in response.text and "worker-2" in response.text
    assert sample_prompt.prompt_set.client.name in response.text
    assert "<html" not in response.text  # a fragment, not a full page


def test_workers_fragment_route_requires_a_login(client):
    assert client.get("/schedules/workers", follow_redirects=False).status_code in (303, 401, 403)


def test_worker_statuses_empty_when_none_ever_reported(db_session):
    assert worker_statuses(db_session, now=NOW) == []


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (8, "8 s"),
        (65, "1 min"),
        (12000, "3 h 20 min"),
    ],
)
def test_format_duration_short(seconds, expected):
    assert format_duration_short(T, seconds) == expected


def test_active_queue_rows_orders_by_priority_then_scheduled_for(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    low = _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], priority=50
    )
    high = _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], priority=200
    )
    # A terminal item must never show up in the active queue view.
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )

    rows = active_queue_rows(db_session)

    assert [row.id for row in rows] == [high.id, low.id]


def test_oldest_queued_age_seconds(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    assert oldest_queued_age_seconds(db_session, now=NOW) is None

    _make_queue_item(
        db_session,
        prompt=sample_prompt,
        client=client,
        model=seed["model"],
        persona=seed["persona"],
        queued_at=NOW - timedelta(minutes=10),
    )

    age = oldest_queued_age_seconds(db_session, now=NOW)
    assert age == pytest.approx(600, abs=1)


def test_history_batches_groups_by_batch_id(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    batch_id = uuid.uuid4()
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        batch_id=batch_id, status="done", scheduled_for=NOW,
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        batch_id=batch_id, status="error", scheduled_for=NOW, last_error="boom",
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        status="done", scheduled_for=NOW - timedelta(hours=1),
    )

    batches, has_next = history_batches(db_session, status_filter="all", search="", page=1, page_size=50)

    assert has_next is False
    assert len(batches) == 2
    grouped = next(b for b in batches if b.total == 2)
    assert grouped.done_count == 1
    assert grouped.error_count == 1
    assert len(grouped.items) == 2
    singleton = next(b for b in batches if b.total == 1)
    assert singleton.done_count == 1


def test_history_batches_status_filter_errors(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        status="error", last_error="boom",
    )

    batches, _ = history_batches(db_session, status_filter="errors", search="", page=1, page_size=50)

    assert len(batches) == 1
    assert batches[0].error_count == 1


def test_history_batches_status_filter_skipped(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        status="skipped", skip_reason="dry_run",
    )

    batches, _ = history_batches(db_session, status_filter="skipped", search="", page=1, page_size=50)

    assert len(batches) == 1
    assert batches[0].skipped_count == 1


def test_history_batches_search_matches_prompt_or_client(db_session, seed, sample_prompt, second_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )
    _make_queue_item(
        db_session, prompt=second_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )

    batches, _ = history_batches(db_session, status_filter="all", search="second prompt", page=1, page_size=50)

    assert len(batches) == 1
    assert batches[0].items[0].prompt_text == second_prompt.text


def test_history_batches_search_matches_nothing_returns_empty(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done"
    )

    batches, has_next = history_batches(db_session, status_filter="all", search="nothing matches this", page=1, page_size=50)

    assert batches == []
    assert has_next is False


def test_history_batches_pagination(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    for i in range(3):
        _make_queue_item(
            db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
            status="done", scheduled_for=NOW - timedelta(minutes=i),
        )

    page1, has_next1 = history_batches(db_session, status_filter="all", search="", page=1, page_size=2)
    page2, has_next2 = history_batches(db_session, status_filter="all", search="", page=2, page_size=2)

    assert len(page1) == 2
    assert has_next1 is True
    assert len(page2) == 1
    assert has_next2 is False


def test_queue_summary_counts_by_status(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="queued")
    _make_queue_item(db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="queued")
    _make_queue_item(db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="leased")
    _make_queue_item(db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"], status="done")

    summary = queue_summary(db_session)

    assert summary.queued_count == 2
    assert summary.leased_count == 1
    assert summary.deferred_count == 0


def _make_schedule(db_session: Session, *, client: Client, prompt: Prompt, created_by, **overrides):
    from datetime import date

    from app.models.schedule import RunSchedule

    defaults = dict(
        client_id=client.id,
        target_type="prompt",
        target_id=prompt.id,
        model_ids=[1],
        market_id=None,
        persona_ids=[1],
        frequency="daily",
        time_of_day=NOW.time(),
        timezone="Europe/Prague",
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 1),
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


def test_schedule_health_classifies_by_outcome(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    schedule = _make_schedule(db_session, client=client, prompt=sample_prompt, created_by=admin_user)

    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=schedule.id, status="done", scheduled_for=NOW - timedelta(days=2),
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=schedule.id, status="error", scheduled_for=NOW - timedelta(days=1), last_error="boom",
    )
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=schedule.id, status="skipped", skip_reason="dry_run", scheduled_for=NOW,
    )

    health = schedule_health(db_session)

    tiles = health[schedule.id]
    assert len(tiles) == 3
    assert [t.outcome for t in tiles] == ["done", "error", "skipped"]


def test_schedule_health_error_wins_within_mixed_window(db_session, seed, sample_prompt, second_prompt, admin_user):
    """A window (shared batch_id, a prompt-set fanout) with both a done and an error item

    classifies as 'error' as a whole — the same all-or-nothing rule a CI build uses for its
    overall status, even though most of the window actually succeeded.
    """
    client = sample_prompt.prompt_set.client
    schedule = _make_schedule(
        db_session, client=client, prompt=sample_prompt, created_by=admin_user,
        target_type="prompt_set", target_id=sample_prompt.prompt_set_id,
    )
    batch_id = uuid.uuid4()
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=schedule.id, batch_id=batch_id, status="done", scheduled_for=NOW,
    )
    _make_queue_item(
        db_session, prompt=second_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=schedule.id, batch_id=batch_id, status="error", scheduled_for=NOW, last_error="boom",
    )

    health = schedule_health(db_session)

    tiles = health[schedule.id]
    assert len(tiles) == 1
    assert tiles[0].outcome == "error"


def test_schedule_health_ignores_items_without_schedule(db_session, seed, sample_prompt):
    client = sample_prompt.prompt_set.client
    _make_queue_item(
        db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
        schedule_id=None, status="done",
    )

    assert schedule_health(db_session) == {}


def test_schedule_health_limits_per_schedule(db_session, seed, sample_prompt, admin_user):
    client = sample_prompt.prompt_set.client
    schedule = _make_schedule(db_session, client=client, prompt=sample_prompt, created_by=admin_user)
    for days_ago in range(12):
        _make_queue_item(
            db_session, prompt=sample_prompt, client=client, model=seed["model"], persona=seed["persona"],
            schedule_id=schedule.id, status="done", scheduled_for=NOW - timedelta(days=days_ago),
        )

    health = schedule_health(db_session, limit_per_schedule=5)

    tiles = health[schedule.id]
    assert len(tiles) == 5
    # The 5 most recent windows overall are days_ago 0..4; kept tiles stay oldest-first, so the
    # kept set's oldest is 4 days ago and its newest (last) is today.
    assert tiles[0].scheduled_for == NOW - timedelta(days=4)
    assert tiles[-1].scheduled_for == NOW


# ---------------------------------------------------------------------------
# skipped_summary / skip reasons (docs/TASKS_SCHEDULER_OPS.md T5, design decision 10)
# ---------------------------------------------------------------------------


def _skipped(db_session, sample_prompt, seed, reason, *, finished_at=NOW, client=None, **overrides):
    return _make_queue_item(
        db_session, prompt=sample_prompt, client=client or sample_prompt.prompt_set.client, model=seed["model"],
        persona=seed["persona"], status="skipped", skip_reason=reason, finished_at=finished_at, **overrides,
    )


def test_skipped_summary_counts_by_reason_most_frequent_first_and_ignores_dry_run(db_session, seed, sample_prompt):
    for reason, times in (("quota_exceeded", 3), ("worker_down", 1), ("dry_run", 40)):
        for _ in range(times):
            _skipped(db_session, sample_prompt, seed, reason)

    rows = skipped_summary(db_session, since=NOW - timedelta(hours=24))

    assert rows == [("quota_exceeded", 3), ("worker_down", 1)]  # dry_run is by design, never a "problem"


def test_skipped_summary_only_counts_the_window_and_the_status(db_session, seed, sample_prompt):
    _skipped(db_session, sample_prompt, seed, "quota_exceeded", finished_at=NOW - timedelta(hours=2))
    _skipped(db_session, sample_prompt, seed, "quota_exceeded", finished_at=NOW - timedelta(hours=30))  # too old
    _make_queue_item(  # an error is not a skip
        db_session, prompt=sample_prompt, client=sample_prompt.prompt_set.client, model=seed["model"],
        persona=seed["persona"], status="error", finished_at=NOW,
    )

    assert skipped_summary(db_session, since=NOW - timedelta(hours=24)) == [("quota_exceeded", 1)]


def test_skipped_summary_can_be_limited_to_one_client(db_session, seed, sample_prompt):
    other = Client(name="Other client", slug="other-client")
    db_session.add(other)
    db_session.commit()
    _skipped(db_session, sample_prompt, seed, "queue_depth_exceeded")
    _skipped(db_session, sample_prompt, seed, "worker_down", client=other)

    mine = skipped_summary(db_session, since=NOW - timedelta(hours=24), client_id=sample_prompt.prompt_set.client.id)

    assert mine == [("queue_depth_exceeded", 1)]


def _skip_reasons_written_by_the_code() -> set[str]:
    """Every literal `skip_reason` value in app/ — read from the source, so a reason added to the
    worker or the enqueue code without a translation fails the test below instead of showing up as
    a raw code on /schedules.
    """
    import re
    from pathlib import Path

    reasons: set[str] = set()
    for path in (Path("app") / "worker.py", Path("app") / "services" / "queue.py"):
        source = path.read_text(encoding="utf-8")
        # `skip_reason="x"` / `item.skip_reason = "x"`, but not the tuple form's `... skip_reason = "skipped", "x"`
        reasons |= set(re.findall(r'(?<!status, item\.)skip_reason\s*=\s*"(\w+)"', source))
        reasons |= set(re.findall(r'status,\s*item\.skip_reason\s*=\s*"skipped",\s*"(\w+)"', source))
    return reasons


def test_every_skip_reason_the_code_writes_is_a_known_reason():
    from app.models.schedule import SKIP_REASONS

    assert _skip_reasons_written_by_the_code() <= set(SKIP_REASONS)
    assert len(_skip_reasons_written_by_the_code()) >= 5  # the scan itself still finds the reasons


@pytest.mark.parametrize("locale", ["en", "de"])
def test_every_skip_reason_has_a_translation(locale):
    from app.models.schedule import SKIP_REASONS

    t = get_translator(locale)
    for reason in SKIP_REASONS:
        key = f"schedules.skip_reason_{reason}"
        assert t(key) != key, f"{key} is missing from {locale}.json"
    assert set(skip_reason_labels(t)) == set(SKIP_REASONS)


def test_schedules_page_shows_the_skipped_banner_and_translated_history(admin_client, db_session, seed, sample_prompt):
    real_now = datetime.now(timezone.utc)
    _skipped(db_session, sample_prompt, seed, "quota_exceeded", finished_at=real_now - timedelta(hours=1))
    _skipped(db_session, sample_prompt, seed, "dry_run", finished_at=real_now - timedelta(hours=1))

    page = admin_client.get("/schedules?view=history")

    assert page.status_code == 200
    assert "1 run(s) skipped in the last 24 hours" in page.text  # dry_run not counted
    assert "Daily quota reached" in page.text
    assert ">quota_exceeded<" not in page.text  # the raw code is no longer what the user reads


def test_schedules_page_has_no_banner_when_nothing_was_skipped(admin_client):
    assert "skipped in the last 24 hours" not in admin_client.get("/schedules").text
