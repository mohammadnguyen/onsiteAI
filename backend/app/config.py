"""Application configuration.

Loads typed settings from environment variables, optionally seeded by a
per-environment ``.env.{APP_ENV}`` file in the current working directory.

``APP_ENV`` is the single canonical environment selector. The legacy
``ENVIRONMENT`` env var is still readable for one transitional release and
is compared against ``APP_ENV`` to fail-fast on conflicting values; reading
:attr:`Settings.environment` returns :attr:`Settings.app_env` for
backward compatibility. See ADR 0002 and
``docs/operations/env-and-secrets.md``.

In any non-development environment (``test`` / ``staging`` / ``production``)
the loader fails fast on:

* placeholder or short JWT secrets,
* empty origin lists in ``CORS_ALLOWED_ORIGINS``,
* a wildcard ``*`` entry in ``CORS_ALLOWED_ORIGINS``,
* inconsistent ``APP_ENV`` / ``ENVIRONMENT`` values.

The startup logger (``app.main``) emits *only* boolean presence/validity
for secrets: never a value, hash, prefix, or any value-derived fingerprint.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class SettingsValidationError(RuntimeError):
    """Raised by :func:`get_settings` when settings construction fails.

    Wraps Pydantic's :class:`ValidationError` so the secret values
    inside the failed input dict never reach stderr. The wrapped error
    is intentionally NOT chained (``raise ... from None``) — chained
    ValidationError tracebacks include ``input_value=`` which Pydantic
    formats as the full input dict and would leak ``jwt_secret``.
    """

# Case-insensitive set of values that may never be used as a JWT secret
# outside development. Comparison is against the stripped, lowercased
# value of the configured secret.
_PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "",
        "change-me",
        "change-me-in-prod",
        "changeme",
        "placeholder",
        "secret",
    }
)

_MIN_JWT_SECRET_LEN = 32

# Environments where the dev-only permissive behaviour applies. ``test``
# is intentionally treated as non-dev so production gates have CI
# coverage; conftest sets a real (non-placeholder) test JWT secret + a
# non-empty CORS_ALLOWED_ORIGINS value to satisfy the validator.
_DEV_ENVS: frozenset[str] = frozenset({"development"})


def _read_env(name: str) -> str | None:
    """Return ``os.environ[name]`` stripped + lowercased, or ``None`` if unset/blank."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    return stripped.lower()


def _resolve_app_env() -> str:
    """Determine the canonical ``APP_ENV`` from the process environment only.

    Resolution order:

    1. ``APP_ENV`` (canonical).
    2. ``ENVIRONMENT`` (deprecated fallback; emits ``DeprecationWarning``).
    3. ``"development"`` (default).

    This function does *not* read any ``.env`` file. The cross-source
    conflict check between ``APP_ENV`` and ``ENVIRONMENT`` (including
    file-sourced values) happens in :meth:`Settings._validate`.
    """
    app_env = _read_env("APP_ENV")
    if app_env:
        return app_env
    legacy = _read_env("ENVIRONMENT")
    if legacy:
        warnings.warn(
            "ENVIRONMENT env var is set but APP_ENV is not. Falling back to "
            "ENVIRONMENT; this fallback is deprecated and will be removed in "
            "a future release. Set APP_ENV instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy
    return "development"


def _resolve_env_file(app_env: str) -> Path | None:
    """Pick the ``.env`` file for ``app_env`` relative to the current working dir.

    Returns the per-environment file if present; otherwise (and only in
    ``development``) falls back to a legacy ``.env`` for one-file dev
    setups; otherwise ``None`` and ``Settings`` relies on the process
    environment alone.

    The legacy ``.env`` fallback is intentionally restricted to
    ``APP_ENV=development``. Falling back to ``.env`` in test / staging /
    production would silently mix a developer's local dev config into a
    non-dev process — exactly the silent-mixing failure mode this loader
    is supposed to prevent.
    """
    per_env = Path(f".env.{app_env}")
    if per_env.is_file():
        return per_env
    if app_env == "development":
        legacy = Path(".env")
        if legacy.is_file():
            return legacy
    return None


class Settings(BaseSettings):
    """Typed application settings backed by environment variables.

    Construction never trusts the input blindly: the model validator
    refuses to return an instance whose ``app_env`` requires a real
    secret but whose ``jwt_secret`` is a placeholder, whose
    ``CORS_ALLOWED_ORIGINS`` is empty or wildcard, or whose ``APP_ENV``
    contradicts a separately-supplied ``ENVIRONMENT`` value.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Canonical environment selector. ``ENVIRONMENT`` is read via
    # ``legacy_environment`` below for conflict detection only.
    app_env: str = Field(default="development")

    database_url: str = Field(...)
    jwt_secret: str = Field(...)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Maximum number of simultaneously-active admins (role=admin AND
    # is_active=True). Enforced in app.services.users on invite and on
    # promotion to admin; the last active admin can be neither deactivated
    # nor demoted. App-level rule only — no DB constraint, no migration.
    # The app is single-tenant, so this is a global cap.
    max_active_admins: int = 3

    # Audit E2: per-minute cap on auth attempts, keyed on (client IP, email)
    # for login and on client IP for refresh. 0 disables the limiter (the
    # test suite sets 0 so shared-client login flows stay deterministic; a
    # focused test re-enables it). The default throttles online password
    # brute-force against the public login endpoint without a datastore —
    # the app is single-node so an in-process counter is sufficient for V1.
    auth_rate_limit_per_minute: int = 10

    # Security audit 2026-07: a SEPARATE per-IP login cap (on top of the
    # per-(ip,email) cap) to blunt password-spray — one host trying one
    # password across many known emails. Higher than the per-account cap
    # so a shared office NAT isn't locked out at login time. 0 disables.
    login_ip_rate_limit_per_minute: int = 30

    # Comma-separated list of allowed origins. Example value:
    #   CORS_ALLOWED_ORIGINS=https://admin.example.com,https://app.example.com
    # ``NoDecode`` tells pydantic-settings not to JSON-decode the env-var
    # string (which would fail on a comma-separated list); the
    # ``mode="before"`` field validator below performs the split.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ---- Evidence storage (DEC-EVIDENCE-001) --------------------------
    # Backend switch: "local" (filesystem; the dev/test default) or "s3"
    # (any S3-compatible endpoint — Tigris in staging/production). The
    # model validator below requires "s3" with full connection settings
    # in staging/production; credentials come from the standard AWS env
    # vars injected by ``flyctl secrets`` (never from this file).
    evidence_storage_backend: str = "local"
    evidence_local_root: str = "./var/evidence"
    evidence_s3_endpoint_url: str | None = None
    evidence_s3_bucket: str | None = None
    # 25 MiB — covers voice memos and phone photos; raise only with
    # evidence of real captures exceeding it (H1 friction log).
    evidence_max_upload_bytes: int = 25 * 1024 * 1024

    # Legacy input alias for ``ENVIRONMENT``. Never read this attribute
    # from application code — use :attr:`environment` (which mirrors
    # ``app_env``) instead. Existence on the model is purely so the
    # validator can compare it against ``app_env`` and emit a
    # deprecation / conflict signal.
    legacy_environment: str | None = Field(
        default=None,
        validation_alias="environment",
        description="Deprecated; use APP_ENV. Retained for conflict detection only.",
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("database_url", mode="after")
    @classmethod
    def _ensure_asyncpg_driver(cls, v: str) -> str:
        """Coerce bare ``postgresql://`` to ``postgresql+asyncpg://``.

        Fly Managed Postgres' ``fly mpg attach`` injects ``DATABASE_URL``
        as ``postgresql://...`` (no driver suffix). SQLAlchemy then
        defaults to the sync ``psycopg2`` dialect, which this project
        does not ship (asyncpg is the chosen async driver). The
        coercion is one-way: a URL that already starts with the
        explicit asyncpg suffix is returned unchanged.
        """
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Parse a comma-separated string into a stripped, non-empty list.

        Empty entries (after :meth:`str.strip`) are dropped. Non-string
        inputs (e.g. an already-parsed list, as passed by unit tests)
        pass through unchanged.
        """
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        # 1. APP_ENV vs ENVIRONMENT consistency.
        if self.legacy_environment is not None:
            legacy = self.legacy_environment.strip().lower()
            if legacy != self.app_env:
                raise ValueError(
                    "Conflicting environment configuration: "
                    f"APP_ENV={self.app_env!r} but ENVIRONMENT={legacy!r}. "
                    "Use APP_ENV only; ENVIRONMENT is deprecated."
                )
            warnings.warn(
                "ENVIRONMENT env var is deprecated; use APP_ENV instead. "
                "ENVIRONMENT will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )

        # 2. Non-development gates: fail fast on insecure values.
        if self.app_env not in _DEV_ENVS:
            secret = self.jwt_secret.strip()
            if secret.lower() in _PLACEHOLDER_SECRETS:
                raise ValueError(
                    f"Placeholder JWT_SECRET rejected for APP_ENV={self.app_env!r}. "
                    f"Set a real secret of at least {_MIN_JWT_SECRET_LEN} characters."
                )
            if len(secret) < _MIN_JWT_SECRET_LEN:
                raise ValueError(
                    f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LEN} characters "
                    f"for APP_ENV={self.app_env!r}; got {len(secret)} chars."
                )
            if not self.cors_allowed_origins:
                raise ValueError(
                    f"CORS_ALLOWED_ORIGINS must be non-empty for "
                    f"APP_ENV={self.app_env!r}; specify explicit "
                    "comma-separated origins."
                )
            if any(origin == "*" for origin in self.cors_allowed_origins):
                raise ValueError(
                    f"Wildcard '*' is not allowed in CORS_ALLOWED_ORIGINS for "
                    f"APP_ENV={self.app_env!r}; list explicit origins."
                )

        # 3. Evidence storage (DEC-EVIDENCE-001). Backend value must be
        # known; staging/production must not silently run on the local
        # filesystem (VMs are stateless — evidence would be lost);
        # selecting s3 anywhere requires full connection settings.
        if self.evidence_storage_backend not in {"local", "s3"}:
            raise ValueError(
                "EVIDENCE_STORAGE_BACKEND must be 'local' or 's3'; got "
                f"{self.evidence_storage_backend!r}."
            )
        if self.app_env in {"staging", "production"} and (
            self.evidence_storage_backend != "s3"
        ):
            raise ValueError(
                f"APP_ENV={self.app_env!r} requires "
                "EVIDENCE_STORAGE_BACKEND=s3 — the local filesystem "
                "backend would silently lose evidence on stateless VMs."
            )
        if self.evidence_storage_backend == "s3" and (
            not self.evidence_s3_endpoint_url or not self.evidence_s3_bucket
        ):
            raise ValueError(
                "EVIDENCE_STORAGE_BACKEND=s3 requires "
                "EVIDENCE_S3_ENDPOINT_URL and EVIDENCE_S3_BUCKET."
            )

        return self

    @property
    def environment(self) -> str:
        """Backward-compatible alias for :attr:`app_env` (read-only).

        Retained so any future code that reads ``settings.environment``
        continues to compile. Prefer ``settings.app_env`` in new code.
        """
        return self.app_env

    @property
    def jwt_secret_is_valid(self) -> bool:
        """``True`` iff :attr:`jwt_secret` would pass the non-dev validator.

        Used by the startup logger to emit a boolean without exposing
        any portion of the secret value.
        """
        secret = self.jwt_secret.strip()
        return (
            secret != ""
            and secret.lower() not in _PLACEHOLDER_SECRETS
            and len(secret) >= _MIN_JWT_SECRET_LEN
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance with env-aware file loading.

    Wraps Pydantic's :class:`ValidationError` in
    :class:`SettingsValidationError` so the input dict (which contains
    ``jwt_secret``) is never rendered to stderr. The wrapped exception
    is dropped via ``raise ... from None`` to prevent the chained
    ValidationError's repr from appearing in the traceback.
    """
    app_env = _resolve_app_env()
    env_file = _resolve_env_file(app_env)

    # Ensure the resolved value is visible to Settings even if the user
    # only set ENVIRONMENT. Without this, the field would default to
    # "development" and the deprecation path would silently differ from
    # the resolution path.
    os.environ.setdefault("APP_ENV", app_env)

    try:
        if env_file is not None:
            return Settings(_env_file=str(env_file))
        return Settings()
    except ValidationError as exc:
        # Re-raise with only the human-facing messages — no input dict,
        # no field values. The model_validator's messages already name
        # the offending field and the resolved APP_ENV.
        messages: list[str] = []
        for err in exc.errors():
            msg = err.get("msg", "")
            # Pydantic prefixes model-validator messages with
            # ``"Value error, "``; strip it so the surfaced text reads
            # like an ordinary error.
            msg = msg.removeprefix("Value error, ")
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            if err.get("type") == "value_error":
                messages.append(msg)
            else:
                messages.append(f"{loc}: {msg}")
        raise SettingsValidationError(
            f"Settings validation failed (APP_ENV={app_env!r}): " + "; ".join(messages)
        ) from None


def resolved_env_file_path() -> str:
    """Return the loaded env file path or a sentinel for the startup logger."""
    path = _resolve_env_file(_resolve_app_env())
    return str(path) if path is not None else "none (env vars only)"
