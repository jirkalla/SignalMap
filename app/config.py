"""Application configuration, loaded from environment variables / .env."""

import socket
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the SignalMap app.

    All values are read from the process environment, falling back to a
    local .env file when present (see .env.example for the expected keys).
    """

    database_url: str
    google_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # JWT signing key for the login session cookie (app/auth.py). No default — a missing value
    # must fail app startup loudly, never silently fall back to a weak, guessable secret.
    secret_key: str
    # A cookie marked Secure is never sent by a real browser over plain HTTP — only HTTPS. The
    # app runs over plain HTTP until docs/ROADMAP.md §2 (reverse proxy + TLS) is done, so this
    # defaults to False for local/dev; set COOKIE_SECURE=true once the app is actually served
    # over HTTPS, or logins will silently "not stick" (cookie set, then never sent back).
    cookie_secure: bool = False
    # Defaults to development (safe default, same philosophy as cookie_secure above) — lets a
    # script make a real technical decision (e.g. scripts/seed_dev_users.py refusing to run) in
    # place of a comment nobody reads. Also shown as a badge in the page footer to logged-in users
    # when not "production" (docs/TASKS_VERSIONING.md VER-T2).
    environment: Literal["development", "production"] = "development"
    # Baked into the image at build time (dockerfile ARG/ENV, passed from docs/DEPLOYMENT.md §3.1's
    # $SHA) and shown in the footer to logged-in users only (docs/TASKS_VERSIONING.md decision 10).
    # Empty by default on purpose: a plain dev build passes no build args, and a missing value must
    # never fail startup — the opposite of `secret_key` above. The footer simply omits it.
    git_sha: str = ""
    build_time: str = ""
    # Bootstrap-only: read by `scripts/create_admin.py --from-env`, never by the app itself.
    # Local dev convenience so a fresh `docker compose up` + one script call gets you a working
    # admin account without typing a password interactively every time you reset the DB.
    dev_admin_email: str = ""
    dev_admin_name: str = ""
    dev_admin_password: str = ""

    # Scheduler (docs/TASKS_SCHEDULER.md T3). Kill switch for the ticker loop only — the executor
    # keeps draining whatever is already queued even when this is false (design decision 16),
    # since two app instances sharing one database must never both plan on their own schedule.
    scheduler_enabled: bool = True
    # Shadow mode: the ticker still plans and enqueues normally, but the executor marks every
    # claimed item skipped/dry_run and never calls a provider adapter (design decision 15) — the
    # first part of the app that can spend money unattended stays inert until this is explicitly
    # turned off, once quota enforcement (T10) exists.
    scheduler_dry_run: bool = True
    # How late a missed window may run before it's given up on instead of caught up (design
    # decision 11) — checked both when enqueuing (worker was down) and when claiming (worker
    # fell behind). 360 = 6 hours.
    scheduler_grace_period_minutes: int = 360
    # How long a claimed queue item may stay 'leased' before another worker is allowed to
    # reclaim it (design decision 14) — protects against a killed worker leaving items stuck.
    scheduler_lease_minutes: int = 15
    # Identifies this process's row in worker_heartbeats and its lease ownership on run_queue.
    # Defaults to the container's own hostname (Docker assigns one unique per container) rather
    # than a fixed string, so `docker compose up --scale worker=N` (docs/TASKS_SCHEDULER.md T10)
    # gives every replica a distinct identity for free — no per-replica WORKER_NAME needed. Still
    # overridable via the environment for a deployment that wants a more readable name.
    worker_name: str = socket.gethostname()
    # Runs per rolling 24h a client may have before `run_execution.py`'s check_daily_quota starts
    # rejecting/skipping new ones (T10, design decision 26) — the fallback when a client's own
    # `daily_run_limit` column is NULL. A hard cap, unlike the monthly budget below, which only
    # warns: an unbounded schedule must not be able to spend money forever unnoticed.
    scheduler_default_daily_run_limit: int = 50
    # Waiting ('queued') run_queue rows a single client may have at once (T10 point 2) — protects
    # against a runaway set-level schedule (T5b) filling the queue faster than the worker can
    # drain it. Deliberately a single config value, not a per-client column like daily_run_limit:
    # the schema TASKS_SCHEDULER.md documents for this branch has no such column, and one global
    # ceiling is enough to catch "the queue is growing unbounded" regardless of which client caused
    # it.
    scheduler_max_queue_depth_per_client: int = 500
    # Reserved for a future worker that processes several queue items in parallel per provider —
    # today's worker is strictly sequential (one item per loop iteration, no threads/asyncio), so
    # this is not read anywhere yet. Left at 1 (matching that real behavior) rather than removed,
    # so the setting exists and is documented before anything depends on it (T10 point 3: the
    # realistic run volume for this app doesn't need concurrency yet — see docs/TASKS_SCHEDULER.md
    # T10 for the capacity math; horizontal scaling via `worker_name` above is the intended growth
    # path, not in-process concurrency).
    scheduler_provider_concurrency: int = 1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance.

    Cached with lru_cache so the environment is only parsed once per
    process, and so it can be overridden in tests via dependency overrides.
    """
    return Settings()
