"""Application configuration, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the SignalMap app.

    All values are read from the process environment, falling back to a
    local .env file when present (see .env.example for the expected keys).
    """

    database_url: str
    google_api_key: str = ""
    anthropic_api_key: str = ""
    # JWT signing key for the login session cookie (app/auth.py). No default — a missing value
    # must fail app startup loudly, never silently fall back to a weak, guessable secret.
    secret_key: str
    # A cookie marked Secure is never sent by a real browser over plain HTTP — only HTTPS. The
    # app runs over plain HTTP until docs/ROADMAP.md §2 (reverse proxy + TLS) is done, so this
    # defaults to False for local/dev; set COOKIE_SECURE=true once the app is actually served
    # over HTTPS, or logins will silently "not stick" (cookie set, then never sent back).
    cookie_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance.

    Cached with lru_cache so the environment is only parsed once per
    process, and so it can be overridden in tests via dependency overrides.
    """
    return Settings()
