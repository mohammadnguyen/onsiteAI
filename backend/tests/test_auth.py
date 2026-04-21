"""End-to-end tests for the auth endpoints.

All tests hit the real ASGI app via ``httpx.AsyncClient`` and talk to the
real Postgres test database (``sitetracker_test``). The ``client``
fixture overrides ``app.database.get_db`` with the same session+txn the
seeded-admin fixture uses, so the endpoint handlers observe rows the
test has written even though the transaction will be rolled back at
teardown.
"""

import pytest


@pytest.mark.asyncio
async def test_login_valid_returns_token_pair(client, seeded_admin):
    r = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "refresh_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_returns_401(client, seeded_admin):
    r = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "WRONG"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    r = await client.get("/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user(client, seeded_admin, admin_token):
    r = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@example.com"
    # Password hash must never leak through the wire.
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_refresh_returns_new_access_token(
    client, seeded_admin, admin_refresh_token
):
    r = await client.post(
        "/auth/refresh", json={"refresh_token": admin_refresh_token}
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body.get("token_type") == "bearer"


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(client, seeded_admin, admin_token):
    # An access token MUST NOT be accepted where a refresh token is expected.
    r = await client.post("/auth/refresh", json={"refresh_token": admin_token})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_requires_auth_and_returns_204(
    client, seeded_admin, admin_token
):
    r = await client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_logout_without_token_returns_401(client):
    r = await client.post("/auth/logout")
    assert r.status_code == 401
