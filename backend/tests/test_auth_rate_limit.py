"""Audit E2/E3 — auth hardening tests.

* E2 — the login endpoint throttles repeated attempts (429) once enabled.
* E3 — invite rejects a password shorter than the 12-char floor.

(E4 — no raw-driver-text leak on a DB constraint violation — is covered by
``test_jobs.py::test_patch_partial_threshold_violating_db_check_returns_422``,
which exercises the real IntegrityError path Pydantic cannot pre-catch.)
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.core.rate_limit import auth_rate_limiter


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def enable_rate_limit(monkeypatch):
    """Enable a low auth rate limit for one test, then restore + reset state."""
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    auth_rate_limiter.reset()
    yield 3
    get_settings.cache_clear()
    auth_rate_limiter.reset()


@pytest.mark.asyncio
async def test_login_rate_limited_after_cap(client, seeded_admin, enable_rate_limit):
    """After the per-minute cap of failed logins, further attempts return 429 (E2)."""
    body = {"email": "admin@example.com", "password": "wrong-password"}
    # First `cap` attempts are 401 (bad password); the next is throttled.
    for _ in range(enable_rate_limit):
        r = await client.post("/auth/login", json=body)
        assert r.status_code == 401, r.text
    r = await client.post("/auth/login", json=body)
    assert r.status_code == 429, r.text


@pytest.mark.asyncio
async def test_login_not_limited_when_disabled(client, seeded_admin):
    """With the limiter disabled (suite default 0), repeated logins never 429 (E2)."""
    auth_rate_limiter.reset()
    body = {"email": "admin@example.com", "password": "wrong-password"}
    for _ in range(8):
        r = await client.post("/auth/login", json=body)
        assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_invite_rejects_short_password(client, admin_token, seeded_admin):
    """A password below the 12-char floor is rejected at validation (E3)."""
    r = await client.post(
        "/users/invite",
        headers=_auth(admin_token),
        json={
            "full_name": "Shorty",
            "email": "shorty@example.com",
            "role": "contributor",
            "initial_password": "short",
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_invite_accepts_12_char_password(client, admin_token, seeded_admin):
    """A 12-char password passes the floor (E3)."""
    r = await client.post(
        "/users/invite",
        headers=_auth(admin_token),
        json={
            "full_name": "Longy",
            "email": "longy@example.com",
            "role": "contributor",
            "initial_password": "abcdef123456",
        },
    )
    assert r.status_code == 201, r.text
