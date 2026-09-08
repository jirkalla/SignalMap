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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance.

    Cached with lru_cache so the environment is only parsed once per
    process, and so it can be overridden in tests via dependency overrides.
    """
    return Settings()
