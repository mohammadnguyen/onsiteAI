"""Tests for the Category model, the builder-category seed, and ``/categories``.

Covers:

* :func:`seed_builder_categories` inserts 23 rows in the spec order and is
  idempotent when re-run.
* ``GET /categories`` requires a bearer access token and returns the seeded
  rows ordered by ``display_order``.
* ``POST /categories`` is admin-only, 403 for contributor, 409 on duplicate.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.seed import seed_builder_categories
from app.models.category import Category


@pytest.mark.asyncio
async def test_seed_inserts_23_categories(db_session, seed_categories):
    result = await db_session.execute(select(Category).order_by(Category.display_order))
    names = [c.category_name for c in result.scalars().all()]
    assert len(names) == 23
    assert names[0] == "Demolition"
    assert names[-1] == "Miscellaneous"


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session):
    await seed_builder_categories(db_session)
    await seed_builder_categories(db_session)
    result = await db_session.execute(select(func.count()).select_from(Category))
    assert result.scalar() == 23


@pytest.mark.asyncio
async def test_list_categories_requires_auth(client):
    r = await client.get("/categories")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_categories_returns_seeded(client, admin_token, seed_categories):
    r = await client.get("/categories", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 23
    # Rows come back in display_order and only active ones are shown by default.
    assert payload[0]["category_name"] == "Demolition"
    assert payload[-1]["category_name"] == "Miscellaneous"
    assert all(item["is_active"] is True for item in payload)


@pytest.mark.asyncio
async def test_create_category_admin(client, admin_token):
    r = await client.post(
        "/categories",
        json={"category_name": "Site Security", "display_order": 99},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["category_name"] == "Site Security"
    assert body["display_order"] == 99
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_create_category_contributor_forbidden(client, contributor_token):
    r = await client.post(
        "/categories",
        json={"category_name": "Blocked", "display_order": 1},
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_category_duplicate_409(client, admin_token, seed_categories):
    # "Concrete" is in the seed list; re-POSTing it must collide on the
    # unique constraint and be surfaced as 409 by the handler.
    r = await client.post(
        "/categories",
        json={"category_name": "Concrete", "display_order": 50},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 409
