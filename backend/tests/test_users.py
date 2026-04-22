"""HTTP tests for the admin-only users API.

Covers listing, invite (with duplicate-email 409), partial update, and
the RBAC boundary (contributor -> 403). Also asserts that deactivating a
user invalidates their already-issued access tokens — the ``is_active``
check on :func:`app.deps.get_current_user` is what enforces this.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_list_users_admin(
    client, admin_token, seeded_admin, seeded_contributor
):
    r = await client.get(
        "/users", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin@example.com", "contributor@example.com"}.issubset(emails)
    # No user record should ever leak the password hash.
    for u in r.json():
        assert "password_hash" not in u


@pytest.mark.asyncio
async def test_list_users_contributor_403(client, contributor_token):
    r = await client.get(
        "/users", headers={"Authorization": f"Bearer {contributor_token}"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_invite_admin_creates_user(client, admin_token):
    r = await client.post(
        "/users/invite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "full_name": "New User",
            "email": "newbie@example.com",
            "role": "contributor",
            "initial_password": "secret-initial-password",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "newbie@example.com"
    assert body["role"] == "contributor"
    assert body["full_name"] == "New User"
    assert body["is_active"] is True
    # language_preference should default to en when omitted.
    assert body["language_preference"] == "en"
    assert "password_hash" not in body
    # user_id is a well-formed UUID.
    uuid.UUID(body["user_id"])


@pytest.mark.asyncio
async def test_invite_duplicate_email_409(client, admin_token, seeded_admin):
    r = await client.post(
        "/users/invite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "full_name": "Duplicate",
            "email": "admin@example.com",
            "role": "contributor",
            "initial_password": "secret-pw",
        },
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_invite_contributor_403(client, contributor_token):
    r = await client.post(
        "/users/invite",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={
            "full_name": "Nope",
            "email": "nope@example.com",
            "role": "contributor",
            "initial_password": "secret",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_deactivates_user_and_invalidates_token(
    client, admin_token, seeded_contributor, contributor_token
):
    # Contributor can hit /auth/me initially.
    r = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {contributor_token}"}
    )
    assert r.status_code == 200

    # Admin deactivates the contributor.
    r = await client.patch(
        f"/users/{seeded_contributor.user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # Contributor's existing token should now 401.
    r = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {contributor_token}"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_patch_404_on_missing_user(client, admin_token):
    r = await client.patch(
        f"/users/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_contributor_403(client, contributor_token, seeded_admin):
    r = await client.patch(
        f"/users/{seeded_admin.user_id}",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"full_name": "Renamed"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_changes_language_preference(
    client, admin_token, seeded_contributor
):
    """Admin can correct a user's UI language preference."""
    r = await client.patch(
        f"/users/{seeded_contributor.user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"language_preference": "zh"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["language_preference"] == "zh"
    # Unchanged fields are preserved.
    assert body["full_name"] == "Seed Contributor"
    assert body["role"] == "contributor"
    assert body["is_active"] is True
