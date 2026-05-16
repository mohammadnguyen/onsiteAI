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
async def test_create_job_duplicate_code_returns_409(client, admin_token):
    """Mobile Job Management Lite hardening: a second POST /jobs with the
    same ``job_code`` must surface as a 409 with a friendly detail
    rather than SQLAlchemy's default 500.

    The unique-violation translation lives in the route handler itself
    (`app/api/jobs.py::create_job_endpoint`) and only converts UNIQUE
    constraint failures — other IntegrityError causes still fall
    through to a 422 (mirroring the PATCH route).
    """
    # First POST creates the row with a code.
    first = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "First Job", "job_code": "DUP-001"},
    )
    assert first.status_code == 201, first.text

    # Second POST with the same code (different name) must be 409.
    second = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Second Job", "job_code": "DUP-001"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "Job code already exists"


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


# ===========================================================================
# Phase 3 Lite+ correction — explicit-null PATCH semantics.
#
# The original update_job treated "any None means skip", so the Job Settings
# form had no way to clear target_profit_ratio_pct / warning_amber_pct /
# warning_red_pct / contract_value_ex_gst / total_budget_ex_gst back to NULL
# once they had been set. The corrected route uses model_dump(exclude_unset
# =True) so:
#
# * Field omitted from JSON → no change to the column.
# * Field present with explicit null → clear the column.
#
# Tests cover: omit preserves; explicit-null clears; clearing thresholds
# falls back to effective defaults; clearing target removes derived
# margin fields; cross-field DB CHECK violations come back as 422.
# ===========================================================================


@pytest.mark.asyncio
async def test_patch_omit_preserves_existing_value(client, admin_token):
    """PATCH that omits a field must NOT touch its stored value."""
    # Create job with target set up front.
    job = await _create_job(
        client,
        admin_token,
        name="Preserve Test",
        target_profit_ratio_pct="15.00",
        contract_value_ex_gst="200000.00",
    )
    job_id = job["job_id"]
    # PATCH something else; do not mention the target.
    r = await client.patch(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"site_address": "99 New Street"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["site_address"] == "99 New Street"
    assert Decimal(str(body["target_profit_ratio_pct"])) == Decimal("15.00")
    assert Decimal(str(body["contract_value_ex_gst"])) == Decimal("200000.00")


@pytest.mark.asyncio
async def test_patch_explicit_null_clears_target_profit_ratio_pct(
    client, admin_token
):
    """PATCH with explicit null on target_profit_ratio_pct must clear it."""
    job = await _create_job(
        client, admin_token, name="Clear Target", target_profit_ratio_pct="20.00"
    )
    assert Decimal(str(job["target_profit_ratio_pct"])) == Decimal("20.00")

    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"target_profit_ratio_pct": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_profit_ratio_pct"] is None


@pytest.mark.asyncio
async def test_patch_explicit_null_clears_contract_and_budget(
    client, admin_token
):
    job = await _create_job(
        client,
        admin_token,
        name="Clear Money",
        contract_value_ex_gst="200000.00",
        total_budget_ex_gst="180000.00",
    )
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "contract_value_ex_gst": None,
            "total_budget_ex_gst": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract_value_ex_gst"] is None
    assert body["total_budget_ex_gst"] is None


@pytest.mark.asyncio
async def test_patch_explicit_null_clears_warning_thresholds(
    client, admin_token
):
    """Clearing per-job thresholds must restore the system defaults
    (80 / 100) on the embedded summary's effective_warning_*_pct."""
    job = await _create_job(
        client,
        admin_token,
        name="Clear Thresholds",
        warning_amber_pct="60.00",
        warning_red_pct="90.00",
    )
    job_id = job["job_id"]

    # Sanity: stored = override; effective = override (visible on /jobs).
    list_r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    pre = next(j for j in list_r.json() if j["job_id"] == job_id)
    assert Decimal(str(pre["warning_amber_pct"])) == Decimal("60.00")
    assert Decimal(str(pre["warning_red_pct"])) == Decimal("90.00")
    assert Decimal(str(pre["summary"]["effective_warning_amber_pct"])) == Decimal(
        "60.00"
    )

    # Clear both via explicit null.
    r = await client.patch(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"warning_amber_pct": None, "warning_red_pct": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warning_amber_pct"] is None
    assert body["warning_red_pct"] is None

    # After clearing, effective_* falls back to the system defaults.
    list_r2 = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    post = next(j for j in list_r2.json() if j["job_id"] == job_id)
    assert post["warning_amber_pct"] is None
    assert post["warning_red_pct"] is None
    assert Decimal(str(post["summary"]["effective_warning_amber_pct"])) == Decimal(
        "80.00"
    )
    assert Decimal(str(post["summary"]["effective_warning_red_pct"])) == Decimal(
        "100.00"
    )


@pytest.mark.asyncio
async def test_patch_clear_target_removes_derived_margin_fields(
    client, admin_token, db_session
):
    """When target_profit_ratio_pct goes back to NULL, the derived
    target_cost_limit and budget_delta fields on the budget-summary
    envelope must collapse to None (no input → no derivation).

    This is the user-visible cleanup that makes the Target margin panel
    disappear when the user clears the target."""
    job = await _create_job(
        client,
        admin_token,
        name="Margin Cleanup",
        contract_value_ex_gst="200000.00",
        total_budget_ex_gst="188000.00",
        target_profit_ratio_pct="15.00",
    )
    job_id = job["job_id"]

    # Pre-clear: summary carries the derived fields.
    pre = await client.get(
        f"/jobs/{job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    pre_body = pre.json()
    assert Decimal(str(pre_body["target_cost_limit_ex_gst"])) == Decimal("170000.00")
    assert Decimal(str(pre_body["budget_delta_vs_target_cost_ex_gst"])) == Decimal(
        "18000.00"
    )

    # Clear target via PATCH null.
    r = await client.patch(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"target_profit_ratio_pct": None},
    )
    assert r.status_code == 200, r.text

    post = await client.get(
        f"/jobs/{job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    post_body = post.json()
    assert post_body["target_profit_ratio_pct"] is None
    assert post_body["target_cost_limit_ex_gst"] is None
    assert post_body["budget_delta_vs_target_cost_ex_gst"] is None
    # contract + budget remain → budgeted_profit + ratio still derive.
    assert Decimal(str(post_body["budgeted_profit_ex_gst"])) == Decimal("12000.00")
    assert Decimal(str(post_body["budgeted_profit_ratio_pct"])) == Decimal("6.00")


@pytest.mark.asyncio
async def test_patch_partial_threshold_violating_db_check_returns_422(
    client, admin_token
):
    """Cross-field constraint that Pydantic can't see at PATCH time
    (because only one of amber/red is in the payload) must come back
    as 422, not 500. Job has stored amber=70 / red=80; PATCHing red=60
    alone would make amber>=red, violating ck_jobs_warning_amber_lt_red."""
    job = await _create_job(
        client,
        admin_token,
        name="DB Check 422",
        warning_amber_pct="70.00",
        warning_red_pct="80.00",
    )
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"warning_red_pct": "60.00"},  # < stored amber 70
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "ck_jobs_warning_amber_lt_red" in detail


@pytest.mark.asyncio
async def test_patch_clear_one_threshold_with_partner_set_succeeds(
    client, admin_token
):
    """Clearing only amber while red stays set must succeed — the
    NULL-safe CHECK ``warning_amber_pct IS NULL OR …`` allows the
    partial-clear case."""
    job = await _create_job(
        client,
        admin_token,
        name="Partial Clear",
        warning_amber_pct="70.00",
        warning_red_pct="90.00",
    )
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"warning_amber_pct": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warning_amber_pct"] is None
    assert Decimal(str(body["warning_red_pct"])) == Decimal("90.00")


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


# ---------------------------------------------------------------------------
# Job Lifecycle v1A-1: Edit Job Details + Audit Foundation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_job_name_writes_audit_row_with_pre_edit_snapshot(
    client, admin_token
):
    """PATCH job_name writes one job_audit_log row with action='edit'
    and pre-edit job_name_snapshot."""
    job = await _create_job(client, admin_token, name="Smith Reisdence")
    job_id = job["job_id"]

    r = await client.patch(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Smith Residence"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["job_name"] == "Smith Residence"

    audit = await client.get(
        f"/jobs/{job_id}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit.status_code == 200
    rows = audit.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "edit"
    # Pre-edit snapshot must hold the OLD name, the diff says what it became.
    assert row["job_name_snapshot"] == "Smith Reisdence"
    assert row["changed_fields"] == {
        "job_name": {"old": "Smith Reisdence", "new": "Smith Residence"},
    }


@pytest.mark.asyncio
async def test_patch_job_code_duplicate_returns_409(client, admin_token):
    """Two jobs cannot share a job_code; PATCH that collides returns 409
    with the friendly detail mirroring the POST hardening."""
    await _create_job(client, admin_token, name="A", job_code="SMITH01")
    target = await _create_job(client, admin_token, name="B", job_code="SMITH02")

    r = await client.patch(
        f"/jobs/{target['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_code": "SMITH01"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Job code already exists"

    # And no audit row was written for the failed attempt.
    audit = await client.get(
        f"/jobs/{target['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit.status_code == 200
    assert audit.json() == []


@pytest.mark.asyncio
async def test_patch_job_address_writes_audit_row(client, admin_token):
    """PATCH site_address records the change in changed_fields."""
    job = await _create_job(client, admin_token, name="Site Address Job")
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"site_address": "15 Sun St, Sydney NSW 2000"},
    )
    assert r.status_code == 200

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rows = audit.json()
    assert len(rows) == 1
    assert rows[0]["action"] == "edit"
    assert rows[0]["changed_fields"] == {
        "site_address": {"old": None, "new": "15 Sun St, Sydney NSW 2000"},
    }


@pytest.mark.asyncio
async def test_patch_job_multi_field_change_writes_one_audit_row(
    client, admin_token
):
    """One PATCH touching multiple auditable fields writes one row whose
    changed_fields dict carries every diff."""
    job = await _create_job(client, admin_token, name="Original Name")
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "job_name": "New Name",
            "job_code": "NEW01",
            "site_address": "1 Main St",
        },
    )
    assert r.status_code == 200

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rows = audit.json()
    assert len(rows) == 1, "expected exactly one audit row per PATCH"
    row = rows[0]
    assert row["action"] == "edit"
    # All three field diffs in a single audit row's changed_fields.
    assert set(row["changed_fields"].keys()) == {
        "job_name",
        "job_code",
        "site_address",
    }


@pytest.mark.asyncio
async def test_patch_job_no_op_writes_no_audit_row(client, admin_token):
    """PATCH that touches only non-auditable fields (budgets) writes no
    row. PATCH that re-sends the same auditable value writes no row."""
    job = await _create_job(
        client, admin_token, name="No Op Job", job_code="NOOP01"
    )

    # Touch only a non-auditable field — no audit row.
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"contract_value_ex_gst": "100.00"},
    )
    assert r.status_code == 200

    # Re-send the SAME job_name — still no audit row.
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "No Op Job"},
    )
    assert r.status_code == 200

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit.status_code == 200
    assert audit.json() == []


@pytest.mark.asyncio
async def test_patch_job_status_via_api_writes_audit_row_with_archive_action(
    client, admin_token
):
    """No archive UI yet (v1A-2), but the audit infrastructure must
    already produce an 'archive' action when status flips to completed
    via the existing PATCH endpoint."""
    job = await _create_job(client, admin_token, name="Status Audit Job")
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "completed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rows = audit.json()
    assert len(rows) == 1
    assert rows[0]["action"] == "archive"
    assert rows[0]["changed_fields"] == {
        "status": {"old": "active", "new": "completed"},
    }


@pytest.mark.asyncio
async def test_get_job_audit_admin_only_403_for_contributor(
    client, admin_token, contributor_token
):
    """Audit endpoint rejects contributors with 403 (admin-only gate)."""
    job = await _create_job(client, admin_token, name="Audit RBAC Job")
    r = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_job_audit_404_on_missing_job(client, admin_token):
    """Audit endpoint returns 404 when the parent job does not exist
    (v1A-1 only looks up by live job_id; v1A-3 will extend this)."""
    r = await client.get(
        f"/jobs/{uuid.uuid4()}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_job_audit_returns_rows_newest_first(client, admin_token):
    """Audit trail is ordered created_at DESC; multiple events surface
    in reverse chronological order."""
    job = await _create_job(client, admin_token, name="Ordering Job")
    # Three sequential auditable edits.
    await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Ordering Job v2"},
    )
    await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"site_address": "Site B"},
    )
    await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "completed"},
    )

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rows = audit.json()
    assert len(rows) == 3
    # Newest first → the status change is row 0.
    assert rows[0]["action"] == "archive"
    assert "status" in rows[0]["changed_fields"]
    # Then the address edit.
    assert rows[1]["action"] == "edit"
    assert "site_address" in rows[1]["changed_fields"]
    # Then the rename.
    assert rows[2]["action"] == "edit"
    assert "job_name" in rows[2]["changed_fields"]


@pytest.mark.asyncio
async def test_job_audit_log_jsonb_round_trip(client, admin_token):
    """The JSONB ``changed_fields`` payload round-trips cleanly: PATCH
    in, GET /audit out → the dict equals the input shape (string old
    and new values, no Python-only types leaking through)."""
    job = await _create_job(client, admin_token, name="JSONB Job")
    r = await client.patch(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Renamed JSONB Job", "site_address": "Addr 1"},
    )
    assert r.status_code == 200

    audit = await client.get(
        f"/jobs/{job['job_id']}/audit",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rows = audit.json()
    assert len(rows) == 1
    cf = rows[0]["changed_fields"]
    # Every leaf value is a string or null — no Python-only types.
    for field_diff in cf.values():
        assert set(field_diff.keys()) == {"old", "new"}
        for v in field_diff.values():
            assert v is None or isinstance(v, (str, int, float, bool))
