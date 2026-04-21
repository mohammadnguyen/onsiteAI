"""Password hashing and JWT token utilities.

Public surface:

* :func:`hash_password` / :func:`verify_password` — bcrypt password hashing.
  The returned hash is self-describing (algorithm id + cost + salt embedded)
  so no side-channel storage is needed.
* :func:`create_access_token` / :func:`create_refresh_token` — issue short- and
  long-lived JWTs respectively. Both embed a ``type`` discriminator (``access``
  or ``refresh``), an ``exp`` UTC timestamp, and a random ``jti`` so every
  token is uniquely identifiable (useful for future revocation lists).
* :func:`decode_token` — verify signature + expiry and return the decoded
  payload. Raises :class:`jwt.InvalidTokenError` (or a subclass such as
  :class:`jwt.ExpiredSignatureError`) on failure.

Implementation notes:

* We use ``passlib``'s :class:`CryptContext` — the plan template's idiom — but
  disable its one-time wraparound-bug probe (``detect_wrap_bug``) before the
  backend is finalised. That probe passes a 240-byte secret to
  ``bcrypt.hashpw``, which modern ``bcrypt`` (>=4.1) rejects with a
  ``ValueError``; the probe predates the modern ``bcrypt`` package and is
  obsolete for versions published this decade. Skipping it is safe for
  ``bcrypt>=4`` (Phase-1 dep pin) and lets us keep passlib without pinning to
  a superseded ``bcrypt<4``.
* Settings are fetched via :func:`app.config.get_settings` on every call
  rather than cached at import time, so pytest monkeypatching or
  ``cache_clear()`` in tests takes effect immediately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.handlers import bcrypt as _passlib_bcrypt

# Neutralise passlib 1.7.4's obsolete wraparound-bug probe BEFORE the
# CryptContext is created. The probe uses a 240-byte secret which bcrypt>=4.1
# rejects outright. See module docstring for details.
_passlib_bcrypt._BcryptCommon._workrounds_initialized = True

from passlib.context import CryptContext  # noqa: E402

from app.config import get_settings  # noqa: E402

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(pw: str) -> str:
    """Return a bcrypt hash for ``pw`` (algorithm + cost + salt embedded)."""
    return _pwd.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    """Return ``True`` iff ``pw`` matches the hash ``h``."""
    return _pwd.verify(pw, h)


def _encode(data: dict[str, Any], *, kind: str, delta: timedelta) -> str:
    """Internal: stamp ``type``, ``exp``, and ``jti`` onto ``data`` then sign."""
    s = get_settings()
    payload: dict[str, Any] = {
        **data,
        "type": kind,
        "exp": datetime.now(UTC) + delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_access_token(data: dict[str, Any]) -> str:
    """Issue an access token with the configured lifetime."""
    s = get_settings()
    return _encode(data, kind="access", delta=timedelta(minutes=s.access_token_expire_minutes))


def create_refresh_token(data: dict[str, Any]) -> str:
    """Issue a refresh token with the configured lifetime."""
    s = get_settings()
    return _encode(data, kind="refresh", delta=timedelta(days=s.refresh_token_expire_days))


def decode_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry and return the decoded JWT payload."""
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
