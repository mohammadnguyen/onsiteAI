"""Application configuration.

Loads settings from environment variables (and a local ``.env`` file during
development). All settings are exposed via :func:`get_settings`, which caches
the ``Settings`` instance for the process lifetime. Tests can override values
by setting environment variables *before* :func:`get_settings` is first called,
or by clearing the cache with ``get_settings.cache_clear()``.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings backed by environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(...)
    jwt_secret: str = Field(...)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
