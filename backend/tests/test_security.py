"""Tests for ``app.core.security`` — password hashing and JWT token utilities.

Covers the 3 plan-required roundtrip cases (password, access JWT, refresh JWT)
plus a few optional extras: jti uniqueness across tokens, tampered-signature
rejection, and expiry handling via a settings override.
"""

import jwt
import pytest

from app.config import get_settings
from app.core import security

# --- Plan-required tests -------------------------------------------------------


def test_password_hash_roundtrip():
    h = security.hash_password("hunter2")
    assert security.verify_password("hunter2", h) is True
    assert security.verify_password("wrong", h) is False


def test_jwt_roundtrip():
    token = security.create_access_token({"sub": "user-123"})
    payload = security.decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_jwt_refresh_has_correct_type():
    token = security.create_refresh_token({"sub": "user-123"})
    payload = security.decode_token(token)
    assert payload["type"] == "refresh"


# --- Optional extras -----------------------------------------------------------


def test_two_access_tokens_differ():
    """Each issued token must carry a unique ``jti`` so two calls differ."""
    t1 = security.create_access_token({"sub": "user-123"})
    t2 = security.create_access_token({"sub": "user-123"})
    assert t1 != t2
    p1 = security.decode_token(t1)
    p2 = security.decode_token(t2)
    assert p1["jti"] != p2["jti"]


def test_decode_bad_signature_raises():
    """A tampered token must fail signature verification."""
    token = security.create_access_token({"sub": "user-123"})
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(tampered)


def test_decode_expired_raises(monkeypatch):
    """Tokens created with a non-positive expiry window must fail to decode."""
    # Force a negative expiry window by patching the cached settings object.
    settings = get_settings()
    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    token = security.create_access_token({"sub": "user-123"})
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_token(token)
