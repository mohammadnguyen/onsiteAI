"""Unit tests for ``app.config`` (Prod-readiness Slice 1 / ADR 0002).

Covers the env-aware loader, the comma-separated CORS parser, the
APP_ENV / ENVIRONMENT conflict detection, the deprecation warning on
ENVIRONMENT-only setups, and the fail-fast secret + origin gates that
fire in any non-development environment.

Tests construct :class:`Settings` either via env vars + ``Settings()``
(the integration path through pydantic-settings) or via explicit kwargs
(the unit path that bypasses env loading) — whichever is clearer for
the scenario under test. Tests are CWD-isolated via
``monkeypatch.chdir(tmp_path)`` so a stray ``backend/.env*`` file
cannot pollute results.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from app.config import (
    Settings,
    SettingsValidationError,
    _resolve_app_env,
    _resolve_env_file,
    get_settings,
    resolved_env_file_path,
)

# A real-looking 64-char secret that passes the non-dev length + placeholder gates.
_VALID_SECRET = "x" * 64
_VALID_DB_URL = "postgresql+asyncpg://u:p@localhost:5432/db"
_VALID_ORIGIN = "https://admin.example.com"


# -- helper -------------------------------------------------------------


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every settings-relevant env var so tests start from a known state."""
    for name in (
        "APP_ENV",
        "ENVIRONMENT",
        "DATABASE_URL",
        "JWT_SECRET",
        "JWT_ALGORITHM",
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "REFRESH_TOKEN_EXPIRE_DAYS",
        "CORS_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)
    # Reset the get_settings lru_cache between tests so each one re-resolves.
    get_settings.cache_clear()


# -- CORS comma-separated parser ---------------------------------------


def test_cors_origins_split_strips_and_drops_blanks() -> None:
    s = Settings(
        _env_file=None,
        app_env="production",
        database_url=_VALID_DB_URL,
        jwt_secret=_VALID_SECRET,
        cors_allowed_origins="https://admin.example.com, https://app.example.com , ,",
    )
    assert s.cors_allowed_origins == [
        "https://admin.example.com",
        "https://app.example.com",
    ]


def test_cors_origins_already_a_list_passes_through() -> None:
    s = Settings(
        _env_file=None,
        app_env="production",
        database_url=_VALID_DB_URL,
        jwt_secret=_VALID_SECRET,
        cors_allowed_origins=[_VALID_ORIGIN, "https://app.example.com"],
    )
    assert s.cors_allowed_origins == [_VALID_ORIGIN, "https://app.example.com"]


# -- non-development secret gates --------------------------------------


@pytest.mark.parametrize("placeholder", ["change-me-in-prod", "CHANGE-ME-IN-PROD", ""])
def test_placeholder_jwt_secret_rejected_in_production(placeholder: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=_VALID_DB_URL,
            jwt_secret=placeholder,
            cors_allowed_origins=[_VALID_ORIGIN],
        )
    msg = str(exc.value)
    # The error references the placeholder rejection OR the min-length gate
    # (empty string trips length first); either is correct for placeholders.
    assert "Placeholder JWT_SECRET" in msg or "at least 32 characters" in msg


def test_short_jwt_secret_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=_VALID_DB_URL,
            jwt_secret="x" * 16,  # below 32-char minimum
            cors_allowed_origins=[_VALID_ORIGIN],
        )
    assert "at least 32 characters" in str(exc.value)


def test_valid_secret_accepted_in_production() -> None:
    s = Settings(
        _env_file=None,
        app_env="production",
        database_url=_VALID_DB_URL,
        jwt_secret=_VALID_SECRET,
        cors_allowed_origins=[_VALID_ORIGIN],
    )
    assert s.app_env == "production"
    assert s.jwt_secret_is_valid is True


# -- non-development CORS gates ----------------------------------------


def test_empty_cors_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=_VALID_DB_URL,
            jwt_secret=_VALID_SECRET,
            cors_allowed_origins=[],
        )
    assert "CORS_ALLOWED_ORIGINS must be non-empty" in str(exc.value)


def test_wildcard_cors_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=_VALID_DB_URL,
            jwt_secret=_VALID_SECRET,
            cors_allowed_origins=["*"],
        )
    assert "Wildcard '*' is not allowed" in str(exc.value)


def test_wildcard_among_origins_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=_VALID_DB_URL,
            jwt_secret=_VALID_SECRET,
            cors_allowed_origins=[_VALID_ORIGIN, "*"],
        )
    assert "Wildcard '*' is not allowed" in str(exc.value)


# -- staging mirrors production gates ----------------------------------


def test_staging_enforces_same_gates_as_production() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="staging",
            database_url=_VALID_DB_URL,
            jwt_secret="change-me-in-prod",
            cors_allowed_origins=[_VALID_ORIGIN],
        )


# -- development is permissive -----------------------------------------


def test_development_permits_placeholder_secret() -> None:
    s = Settings(
        _env_file=None,
        app_env="development",
        database_url=_VALID_DB_URL,
        jwt_secret="change-me-in-prod",
        cors_allowed_origins=[],
    )
    assert s.app_env == "development"
    # jwt_secret_is_valid reports the absolute (non-dev) validity check,
    # not "would pass the dev validator" — so a dev placeholder is False.
    assert s.jwt_secret_is_valid is False


def test_development_permits_wildcard_cors() -> None:
    s = Settings(
        _env_file=None,
        app_env="development",
        database_url=_VALID_DB_URL,
        jwt_secret="change-me-in-prod",
        cors_allowed_origins=["*"],
    )
    assert s.cors_allowed_origins == ["*"]


# -- APP_ENV vs ENVIRONMENT conflict -----------------------------------


def test_app_env_environment_conflict_via_kwargs_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="development",
            legacy_environment="production",
            database_url=_VALID_DB_URL,
            jwt_secret="change-me-in-prod",
            cors_allowed_origins=[],
        )
    assert "Conflicting environment configuration" in str(exc.value)


def test_app_env_environment_matching_emits_deprecation_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Settings(
            _env_file=None,
            app_env="development",
            legacy_environment="development",
            database_url=_VALID_DB_URL,
            jwt_secret="change-me-in-prod",
            cors_allowed_origins=[],
        )
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "ENVIRONMENT env var is deprecated" in str(w.message)
        for w in caught
    )


# -- env-var driven integration paths ----------------------------------


def test_env_var_app_env_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", _VALID_DB_URL)
    monkeypatch.setenv("JWT_SECRET", _VALID_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{_VALID_ORIGIN},https://app.example.com")

    s = get_settings()
    assert s.app_env == "production"
    assert s.environment == "production"  # backward-compat property
    assert s.cors_allowed_origins == [_VALID_ORIGIN, "https://app.example.com"]


def test_env_var_environment_only_fallback_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", _VALID_DB_URL)
    monkeypatch.setenv("JWT_SECRET", "dev-placeholder")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = get_settings()
    assert s.app_env == "development"
    # Either the resolution-time or the validator-time deprecation suffices.
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_env_var_app_env_and_environment_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", _VALID_DB_URL)
    monkeypatch.setenv("JWT_SECRET", _VALID_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", _VALID_ORIGIN)

    with pytest.raises(SettingsValidationError) as exc:
        get_settings()
    assert "Conflicting environment configuration" in str(exc.value)


def test_get_settings_failure_message_does_not_leak_jwt_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Regression guard: a deliberately failing get_settings() call must NOT
    surface the JWT secret value in its exception message.

    Without the SettingsValidationError wrapper, Pydantic's ValidationError
    repr embeds ``input_value=<full input dict>`` which includes
    ``jwt_secret``. This test pins the leak-prevention behaviour added in
    Prod-readiness Slice 1 / ADR 0002.
    """
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    sentinel_secret = "do-not-leak-this-very-secret-value-1234567890ABCDEF"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", _VALID_DB_URL)
    monkeypatch.setenv("JWT_SECRET", sentinel_secret)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")  # forces non-empty validator to fail

    with pytest.raises(SettingsValidationError) as exc:
        get_settings()
    msg = str(exc.value)
    assert "CORS_ALLOWED_ORIGINS must be non-empty" in msg
    assert sentinel_secret not in msg
    # Also guard against substrings (any 10-char window) leaking.
    for i in range(0, len(sentinel_secret) - 10):
        assert sentinel_secret[i : i + 10] not in msg


# -- env-file resolution -----------------------------------------------


def test_resolve_env_file_prefers_per_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X=legacy\n")
    (tmp_path / ".env.production").write_text("X=prod\n")
    resolved = _resolve_env_file("production")
    assert resolved is not None
    assert resolved.name == ".env.production"


def test_resolve_env_file_falls_back_to_legacy_in_development(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X=legacy\n")
    resolved = _resolve_env_file("development")
    assert resolved is not None
    assert resolved.name == ".env"


def test_resolve_env_file_no_legacy_fallback_outside_development(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A stray .env file in CWD must NOT be loaded in non-dev environments —
    # that would silently mix a developer's local dev config into a
    # test/staging/production process. Per ADR 0002 the legacy fallback is
    # restricted to APP_ENV=development.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ENVIRONMENT=development\n")
    assert _resolve_env_file("test") is None
    assert _resolve_env_file("staging") is None
    assert _resolve_env_file("production") is None


def test_resolve_env_file_none_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    assert _resolve_env_file("production") is None


def test_resolved_env_file_path_returns_sentinel_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    assert resolved_env_file_path() == "none (env vars only)"


# -- _resolve_app_env -------------------------------------------------


def test_resolve_app_env_defaults_to_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    assert _resolve_app_env() == "development"


def test_resolve_app_env_prefers_app_env_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _resolve_app_env() == "staging"


def test_resolve_app_env_strips_and_lowercases(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "  PRODUCTION  ")
    assert _resolve_app_env() == "production"
