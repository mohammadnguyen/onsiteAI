"""Tests for the Task 8 ``/jobs`` HTTP API.

Covers:

* ``POST /jobs`` — 201 for admin, 403 for contributor.
* ``GET /jobs`` — visible to both roles; admin writes are visible.
* ``GET /jobs/{id}`` — returns aliases + category budgets; 404 on missing.
* ``PATCH /jobs/{id}`` — admin-only; 403 for contributor; 404 on missing.
* ``POST /jobs/{id}/aliases`` — admin-only; duplicate normalised alias is
  409 even across different jobs; 404 on missing parent job.
* ``POST /jobs/{id}/category-budgets`` — admin-only; duplicate
  ``(job_id, category_id)`` is 409; unknown category_id is 404; unknown
  parent job is 404.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest


async def _create_job(client, admin_token, *, name: str = "Kelly House", **extra) -> dict:
    """Helper: POST a job as admin and return the JSON body (asserting 201)."""
    body = {"job_name": name, **extra}
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_job_admin_returns_201(client, admin_token):
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Kelly House", "contract_value_ex_gst": "500000.00"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["job_name"] == "Kelly House"
    assert body["status"] == "active"
    assert Decimal(str(body["contract_value_ex_gst"])) == Decimal("500000.00")
    # The admin who POSTed must appear as ``created_by``.
    assert uuid.UUID(body["created_by"])  # parses


@pytest.mark.asyncio
async def test_create_job_contributor_forbidden(client, contributor_token):
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"job_name": "Kelly House"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_job_requires_auth(client):
    r = await client.post("/jobs", json={"job_name": "Kelly House"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_jobs_both_roles(
    client, admin_token, contributor_token, seeded_contributor
):
    """Admin creates a job; both admin and contributor see it in the list."""
    await _create_job(client, admin_token, name="Visible Job")
    for tok in (admin_token, contributor_token):
        r = await client.get(
            "/jobs", headers={"Authorization": f"Bearer {tok}"}
        )
        assert r.status_code == 200
        assert any(j["job_name"] == "Visible Job" for j in r.json())


@pytest.mark.asyncio
async def test_list_jobs_requires_auth(client):
    r = await client.get("/jobs")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_job_returns_aliases_and_budgets(
    client, admin_token, seed_categories
):
    job = await _create_job(client, admin_token, name="With Detail")
    job_id = job["job_id"]

    # add alias
    alias_r = await client.post(
        f"/jobs/{job_id}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Detail", "language_code": "en"},
    )
    assert alias_r.status_code == 201, alias_r.text
    assert alias_r.json()["alias_text_normalized"] == "detail"

    # add budget
    plumbing = seed_categories[8]
    budget_r = await client.post(
        f"/jobs/{job_id}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "category_id": str(plumbing.category_id),
            "budget_amount_ex_gst": "25000.00",
        },
    )
    assert budget_r.status_code == 201, budget_r.text
    budget_body = budget_r.json()
    assert budget_body["category"]["category_name"] == "Plumbing"

    # GET /jobs/{id} returns aliases + category_budgets
    r = await client.get(
        f"/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id
    assert len(body["aliases"]) == 1
    assert body["aliases"][0]["alias_text"] == "Detail"
    assert len(body["category_budgets"]) == 1
    assert body["category_budgets"][0]["category"]["category_name"] == "Plumbing"
    assert Decimal(
        str(body["category_budgets"][0]["budget_amount_ex_gst"])
    ) == Decimal("25000.00")


@pytest.mark.asyncio
async def test_get_job_404(client, admin_token):
    random_id = uuid.uuid4()
    r = await client.get(
        f"/jobs/{random_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_job_admin_updates_fields(client, admin_token):
    job = await _create_job(client, admin_token, name="Original Name")
    job_id = job["job_id"]
    r = await client.patch(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "job_name": "Renamed",
            "site_address": "5 Edmund St",
            "total_budget_ex_gst": "123456.78",
            "status": "completed",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_name"] == "Renamed"
    assert body["site_address"] == "5 Edmund St"
    assert body["status"] == "completed"
    assert Decimal(str(body["total_budget_ex_gst"])) == Decimal("123456.78")


@pytest.mark.asyncio
async def test_patch_job_contributor_forbidden(
    client, admin_token, contributor_token
):
    job = await _create_job(client, admin_token, name="Locked")
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"job_name": "Nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_job_404(client, admin_token):
    random_id = uuid.uuid4()
    r = await client.patch(
        f"/jobs/{random_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Ghost"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_alias_contributor_forbidden(
    client, admin_token, contributor_token
):
    job = await _create_job(client, admin_token, name="With Alias")
    r = await client.post(
        f"/jobs/{job['job_id']}/aliases",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"alias_text": "Nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_add_alias_duplicate_409(client, admin_token):
    """Duplicate alias across jobs (normalised form) must be 409."""
    job_a = await _create_job(client, admin_token, name="Job A")
    job_b = await _create_job(client, admin_token, name="Job B")

    r1 = await client.post(
        f"/jobs/{job_a['job_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Kelly"},
    )
    assert r1.status_code == 201, r1.text

    # Different casing / punctuation collapses to the same normalised form.
    r2 = await client.post(
        f"/jobs/{job_b['job_id']}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "KELLY"},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_alias_404_on_missing_job(client, admin_token):
    random_id = uuid.uuid4()
    r = await client.post(
        f"/jobs/{random_id}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "Ghost"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_budget_contributor_forbidden(
    client, admin_token, contributor_token, seed_categories
):
    job = await _create_job(client, admin_token, name="With Budget")
    plumbing = seed_categories[8]
    r = await client.post(
        f"/jobs/{job['job_id']}/category-budgets",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={
            "category_id": str(plumbing.category_id),
            "budget_amount_ex_gst": "1000.00",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_add_budget_duplicate_409(
    client, admin_token, seed_categories
):
    job = await _create_job(client, admin_token, name="Dup Budget Job")
    plumbing = seed_categories[8]
    payload = {
        "category_id": str(plumbing.category_id),
        "budget_amount_ex_gst": "25000.00",
    }
    r1 = await client.post(
        f"/jobs/{job['job_id']}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert r1.status_code == 201, r1.text

    r2 = await client.post(
        f"/jobs/{job['job_id']}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_budget_404_on_missing_job(
    client, admin_token, seed_categories
):
    random_id = uuid.uuid4()
    plumbing = seed_categories[8]
    r = await client.post(
        f"/jobs/{random_id}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "category_id": str(plumbing.category_id),
            "budget_amount_ex_gst": "1.00",
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_budget_404_on_missing_category(client, admin_token):
    job = await _create_job(client, admin_token, name="No Cat")
    random_cat_id = uuid.uuid4()
    r = await client.post(
        f"/jobs/{job['job_id']}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "category_id": str(random_cat_id),
            "budget_amount_ex_gst": "1.00",
        },
    )
    assert r.status_code == 404
