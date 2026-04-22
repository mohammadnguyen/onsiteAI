"""Tests for the Task T-L ``/suppliers`` HTTP API.

Covers:

* ``GET /suppliers`` — requires auth; both roles see the full list;
  ``?active_only=1`` filters out deactivated suppliers.
* ``POST /suppliers`` — 201 for admin, 403 for contributor, 409 on a
  duplicate normalised name, 422 on an empty name.
* ``PATCH /suppliers/{id}`` — 200 for admin rename / deactivation;
  ``supplier_normalized`` is kept in sync by the model listener; 403 for
  contributor; 404 on missing id; 409 on a rename-to-collide.
* ``POST /suppliers/{id}/aliases`` — 201 for admin; 403 for contributor;
  404 on missing parent; 409 on a duplicate normalised alias; accepts an
  optional ``language_code`` which is round-tripped onto the row.
"""

from __future__ import annotations

import uuid

import pytest


async def _create_supplier(client, admin_token, *, name: str = "Bunnings", **extra) -> dict:
    """Helper: POST a supplier as admin and return the JSON body (asserting 201)."""
    body = {"supplier_name": name, **extra}
    r = await client.post(
        "/suppliers",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_list_suppliers_requires_auth(client):
    r = await client.get("/suppliers")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_suppliers_contributor_sees_all(client, admin_token, contributor_token):
    await _create_supplier(client, admin_token, name="Reece")
    r = await client.get(
        "/suppliers",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 200
    names = [s["supplier_name"] for s in r.json()]
    assert "Reece" in names


@pytest.mark.asyncio
async def test_list_suppliers_admin_sees_all(client, admin_token):
    await _create_supplier(client, admin_token, name="Bunnings")
    await _create_supplier(client, admin_token, name="Reece")
    r = await client.get("/suppliers", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    names = [s["supplier_name"] for s in r.json()]
    assert "Bunnings" in names
    assert "Reece" in names


@pytest.mark.asyncio
async def test_list_suppliers_active_only_filter(client, admin_token):
    active = await _create_supplier(client, admin_token, name="Active Supplier")
    inactive = await _create_supplier(
        client, admin_token, name="Inactive Supplier", is_active=False
    )

    r = await client.get(
        "/suppliers?active_only=1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    returned_ids = {s["supplier_id"] for s in r.json()}
    assert active["supplier_id"] in returned_ids
    assert inactive["supplier_id"] not in returned_ids


@pytest.mark.asyncio
async def test_create_supplier_admin_201(client, admin_token):
    r = await client.post(
        "/suppliers",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": "Bunnings"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["supplier_name"] == "Bunnings"
    # The before_insert listener auto-populates supplier_normalized.
    assert body["supplier_normalized"] == "bunnings"
    assert body["is_active"] is True
    assert uuid.UUID(body["supplier_id"])  # parses


@pytest.mark.asyncio
async def test_create_supplier_contributor_403(client, contributor_token):
    r = await client.post(
        "/suppliers",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"supplier_name": "Blocked"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_supplier_duplicate_name_409(client, admin_token):
    """Second POST with same normalised form must collide with 409."""
    await _create_supplier(client, admin_token, name="Bunnings")

    # Different casing + surrounding whitespace still collapses to the
    # same normalised form.
    r = await client.post(
        "/suppliers",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": "  bunnings  "},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_patch_supplier_admin_200(client, admin_token):
    supplier = await _create_supplier(client, admin_token, name="Original Supplier")
    r = await client.patch(
        f"/suppliers/{supplier['supplier_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": "Renamed Supplier"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supplier_name"] == "Renamed Supplier"
    # The before_update listener re-syncs supplier_normalized.
    assert body["supplier_normalized"] == "renamedsupplier"


@pytest.mark.asyncio
async def test_patch_supplier_contributor_403(client, admin_token, contributor_token):
    supplier = await _create_supplier(client, admin_token, name="Locked Supplier")
    r = await client.patch(
        f"/suppliers/{supplier['supplier_id']}",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"supplier_name": "Nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_supplier_404(client, admin_token):
    random_id = uuid.uuid4()
    r = await client.patch(
        f"/suppliers/{random_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": "Ghost"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_supplier_to_duplicate_name_409(client, admin_token):
    """Renaming supplier B to collide with supplier A's normalised form is 409."""
    await _create_supplier(client, admin_token, name="Bunnings")
    supplier_b = await _create_supplier(client, admin_token, name="Reece")

    r = await client.patch(
        f"/suppliers/{supplier_b['supplier_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": "BUNNINGS"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_patch_supplier_deactivate(client, admin_token):
    supplier = await _create_supplier(client, admin_token, name="Deactivate Me")
    r = await client.patch(
        f"/suppliers/{supplier['supplier_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    # It must no longer appear in the active_only list.
    list_r = await client.get(
        "/suppliers?active_only=1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_r.status_code == 200
    returned_ids = {s["supplier_id"] for s in list_r.json()}
    assert supplier["supplier_id"] not in returned_ids


@pytest.mark.asyncio
async def test_add_alias_admin_201(client, admin_token):
    supplier = await _create_supplier(client, admin_token, name="Bunnings")
    r = await client.post(
        f"/suppliers/{supplier['supplier_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "BWC"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["alias_text"] == "BWC"
    assert body["alias_text_normalized"] == "bwc"
    assert body["supplier_id"] == supplier["supplier_id"]


@pytest.mark.asyncio
async def test_add_alias_contributor_403(client, admin_token, contributor_token):
    supplier = await _create_supplier(client, admin_token, name="With Alias")
    r = await client.post(
        f"/suppliers/{supplier['supplier_id']}/aliases",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"alias_text": "Nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_add_alias_duplicate_normalized_409(client, admin_token):
    """Duplicate alias across suppliers (normalised form) must be 409."""
    supplier_a = await _create_supplier(client, admin_token, name="Supplier A")
    supplier_b = await _create_supplier(client, admin_token, name="Supplier B")

    r1 = await client.post(
        f"/suppliers/{supplier_a['supplier_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Bunnings"},
    )
    assert r1.status_code == 201, r1.text

    # Upper-cased re-add under a different supplier still collides.
    r2 = await client.post(
        f"/suppliers/{supplier_b['supplier_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "BUNNINGS"},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_alias_404_on_missing_supplier(client, admin_token):
    random_id = uuid.uuid4()
    r = await client.post(
        f"/suppliers/{random_id}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Ghost"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_alias_with_language_code(client, admin_token):
    supplier = await _create_supplier(client, admin_token, name="Lang Supplier")
    r = await client.post(
        f"/suppliers/{supplier['supplier_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Bunnings AU", "language_code": "en"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["language_code"] == "en"
    assert body["alias_text"] == "Bunnings AU"


@pytest.mark.asyncio
async def test_create_supplier_rejects_empty_name(client, admin_token):
    r = await client.post(
        "/suppliers",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"supplier_name": ""},
    )
    assert r.status_code == 422
