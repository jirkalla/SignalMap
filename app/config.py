"""Application configuration, loaded from environment variables / .env."""

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
    # place of a comment nobody reads. Not otherwise read by the app itself.
    environment: Literal["development", "production"] = "development"
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
    # A fixed default is fine for the single-worker deployment this branch ships (decision 3);
    # a future multi-worker setup would need this set uniquely per instance via the environment.
    worker_name: str = "scheduler-1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance.

    Cached with lru_cache so the environment is only parsed once per
    process, and so it can be overridden in tests via dependency overrides.
    """
    return Settings()
