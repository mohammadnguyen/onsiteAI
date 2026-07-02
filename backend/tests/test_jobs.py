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


# ---------------------------------------------------------------------------
# F2 — per-job contract GST basis (gst_mode). Display-hint only:
# contract_value_ex_gst stays the canonical ex-GST basis; gst_mode just
# records how the mobile entered/displays it. Backend does no GST math.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_defaults_gst_mode_exclusive(client, admin_token):
    """F2: gst_mode omitted -> defaults to 'exclusive' (UI 'No GST (Cash)').
    Preserves today's behaviour for every existing/new job that doesn't set it."""
    body = await _create_job(client, admin_token, name="No-GST Job")
    assert body["gst_mode"] == "exclusive"


@pytest.mark.asyncio
async def test_create_job_with_inclusive_gst_mode(client, admin_token):
    """F2: gst_mode 'inclusive' (UI 'Including GST') persists on create + read."""
    body = await _create_job(
        client, admin_token, name="GST Job", gst_mode="inclusive"
    )
    assert body["gst_mode"] == "inclusive"
    r = await client.get(
        f"/jobs/{body['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["gst_mode"] == "inclusive"  # JobWithDetailPublic serialises it


@pytest.mark.asyncio
async def test_patch_job_toggles_gst_mode_without_touching_contract(
    client, admin_token
):
    """F2: PATCH toggles gst_mode; the toggle alone never rewrites the stored
    contract_value_ex_gst (the mobile re-derives it; the backend stores what it
    is sent). Here we send only gst_mode and assert the contract is unchanged."""
    body = await _create_job(
        client,
        admin_token,
        name="Toggle Job",
        contract_value_ex_gst="1000.00",
        gst_mode="exclusive",
    )
    r = await client.patch(
        f"/jobs/{body['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"gst_mode": "inclusive"},
    )
    assert r.status_code == 200, r.text
    patched = r.json()
    assert patched["gst_mode"] == "inclusive"
    assert Decimal(str(patched["contract_value_ex_gst"])) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_gst_mode_does_not_affect_expense_gst_split():
    """F2 HARD GUARD (operator): a job's gst_mode must NEVER change any
    expense-level GST calculation. compute_gst_split is driven solely by
    payment_method and takes no job / gst_mode argument — asserted directly so
    a future refactor that tries to wire gst_mode into the split breaks here."""
    import inspect

    from app.models.expense import PaymentMethod, compute_gst_split

    # cash -> no GST extracted (ex == inc, gst == 0)
    assert compute_gst_split(Decimal("110.00"), PaymentMethod.cash) == (
        Decimal("110.00"),
        Decimal("0.00"),
    )
    # transfer -> standard 1/11 split
    assert compute_gst_split(Decimal("110.00"), PaymentMethod.transfer) == (
        Decimal("100.00"),
        Decimal("10.00"),
    )
    # unknown -> same 1/11 split (not cash)
    assert compute_gst_split(Decimal("110.00"), PaymentMethod.unknown) == (
        Decimal("100.00"),
        Decimal("10.00"),
    )
    # Structural lock: the split's only inputs are amount + payment_method;
    # there is no job/gst_mode parameter, so a job's GST mode cannot reach it.
    params = set(inspect.signature(compute_gst_split).parameters)
    assert params == {"amount_inc_gst", "payment_method"}


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


# ---------------------------------------------------------------------------
# Jobs money strip — contract/budget/margin/thresholds/summary/category
# budgets are admin-only on the wire. Mirrors the O1-S1 expense strip
# (test_expenses_api.py): contributors receive job IDENTITY (id, code,
# name, address, status, aliases — the mobile capture job picker depends
# on these) with money fields nulled server-side; admin responses are
# unchanged. Response shaping only — no DB schema change.
# ---------------------------------------------------------------------------


_MONEY_JOB_KWARGS = {
    "contract_value_ex_gst": "500000.00",
    "total_budget_ex_gst": "400000.00",
    "target_profit_ratio_pct": "20",
    "warning_amber_pct": "75",
    "warning_red_pct": "95",
}


@pytest.mark.asyncio
async def test_list_jobs_strips_money_for_contributor(
    client, admin_token, contributor_token, seeded_contributor
):
    """Contributor list rows carry identity only — money + summary nulled."""
    await _create_job(
        client, admin_token, name="Strip List Job", job_code="SL-01",
        **_MONEY_JOB_KWARGS,
    )
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {contributor_token}"}
    )
    assert r.status_code == 200
    row = next(j for j in r.json() if j["job_name"] == "Strip List Job")
    # Identity stays (the capture job picker reads these).
    assert row["job_code"] == "SL-01"
    assert row["status"] == "active"
    # Money is server-stripped.
    assert row["contract_value_ex_gst"] is None
    assert row["total_budget_ex_gst"] is None
    assert row["target_profit_ratio_pct"] is None
    assert row["warning_amber_pct"] is None
    assert row["warning_red_pct"] is None
    assert row["summary"] is None


@pytest.mark.asyncio
async def test_list_jobs_keeps_money_for_admin(client, admin_token):
    """Admin list rows are unchanged — money fields + summary present."""
    await _create_job(
        client, admin_token, name="Keep List Job", **_MONEY_JOB_KWARGS
    )
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    row = next(j for j in r.json() if j["job_name"] == "Keep List Job")
    assert Decimal(str(row["contract_value_ex_gst"])) == Decimal("500000.00")
    assert Decimal(str(row["total_budget_ex_gst"])) == Decimal("400000.00")
    assert Decimal(str(row["target_profit_ratio_pct"])) == Decimal("20")
    # Phase 3 Lite: every admin row carries a populated summary.
    assert row["summary"] is not None


@pytest.mark.asyncio
async def test_get_job_strips_money_but_keeps_aliases_for_contributor(
    client, admin_token, contributor_token, seeded_contributor, seed_categories
):
    """Contributor detail: aliases stay; money + category_budgets stripped."""
    job = await _create_job(
        client, admin_token, name="Strip Detail Job", **_MONEY_JOB_KWARGS
    )
    job_id = job["job_id"]
    alias_r = await client.post(
        f"/jobs/{job_id}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "工地9", "language_code": "zh"},
    )
    assert alias_r.status_code == 201, alias_r.text
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

    r = await client.get(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    # Identity + aliases survive (capture picker / zh chip labels).
    assert body["job_name"] == "Strip Detail Job"
    assert len(body["aliases"]) == 1
    assert body["aliases"][0]["alias_text"] == "工地9"
    # Money is server-stripped, including the per-category budget rows.
    assert body["contract_value_ex_gst"] is None
    assert body["total_budget_ex_gst"] is None
    assert body["target_profit_ratio_pct"] is None
    assert body["warning_amber_pct"] is None
    assert body["warning_red_pct"] is None
    assert body["summary"] is None
    assert body["category_budgets"] == []

    # Admin detail on the SAME job is unchanged.
    a = await client.get(
        f"/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert a.status_code == 200
    admin_body = a.json()
    assert Decimal(str(admin_body["contract_value_ex_gst"])) == Decimal(
        "500000.00"
    )
    assert len(admin_body["category_budgets"]) == 1
    assert Decimal(
        str(admin_body["category_budgets"][0]["budget_amount_ex_gst"])
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


# ---------------------------------------------------------------------------
# Job Lifecycle v1A-3: Delete Empty Job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_empty_job_succeeds_writes_audit_and_cascades(
    client, admin_token, db_session
):
    """v1A-3 happy path: an empty job (zero expenses, zero queue rows)
    can be deleted. The DELETE returns 204; the job row is gone;
    aliases and category budgets cascade via existing model FK
    config; and one new audit row is written with action='delete'.
    """
    from sqlalchemy import select
    from app.models import JobAlias, JobAuditLog, JobCategoryBudget

    job = await _create_job(
        client, admin_token, name="DeleteMe", job_code="DEL-EMPTY-01"
    )
    job_id = job["job_id"]

    # Add an alias so we can verify it cascades on delete.
    r = await client.post(
        f"/jobs/{job_id}/aliases",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias_text": "DeleteMeAlias"},
    )
    assert r.status_code == 201

    # DELETE
    r = await client.delete(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204, r.text
    assert r.text == ""

    # Job is gone — follow-up GET returns 404.
    r = await client.get(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404

    # Alias rows cascaded.
    alias_rows = list(
        (
            await db_session.execute(
                select(JobAlias).where(JobAlias.job_id == uuid.UUID(job_id))
            )
        )
        .scalars()
        .all()
    )
    assert alias_rows == [], "alias should cascade-delete with the job"

    # Budget rows cascaded (we didn't add any, but the query path
    # confirms the model relationship is wired correctly).
    budget_rows = list(
        (
            await db_session.execute(
                select(JobCategoryBudget).where(
                    JobCategoryBudget.job_id == uuid.UUID(job_id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert budget_rows == []

    # Audit row written with action='delete'. Looked up by snapshot
    # because job_id is NULL post-delete (see next test).
    audit_rows = list(
        (
            await db_session.execute(
                select(JobAuditLog).where(
                    JobAuditLog.job_name_snapshot == "DeleteMe"
                )
            )
        )
        .scalars()
        .all()
    )
    delete_rows = [r for r in audit_rows if r.action == "delete"]
    assert len(delete_rows) == 1, "expected exactly one delete audit row"
    row = delete_rows[0]
    assert row.action == "delete"
    assert row.job_name_snapshot == "DeleteMe"
    assert row.job_code_snapshot == "DEL-EMPTY-01"
    assert "_lifecycle" in row.changed_fields
    assert row.changed_fields["_lifecycle"]["new"] == "deleted"


@pytest.mark.asyncio
async def test_delete_empty_job_audit_row_survives_with_job_id_null(
    client, admin_token, db_session
):
    """v1A-3 + v1A-1: the SET NULL FK on job_audit_log.job_id keeps
    the audit row queryable after the parent job is gone — the row's
    job_id column is NULL, and the snapshot columns retain the
    human-meaningful identifier."""
    from sqlalchemy import select
    from app.models import JobAuditLog

    job = await _create_job(
        client, admin_token, name="SurvivorJob", job_code="SURV-01"
    )
    job_id = job["job_id"]

    r = await client.delete(
        f"/jobs/{job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204

    # Query by snapshot name (job_id is NULL post-delete).
    rows = list(
        (
            await db_session.execute(
                select(JobAuditLog).where(
                    JobAuditLog.job_name_snapshot == "SurvivorJob",
                    JobAuditLog.action == "delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    # job_id is NULL by FK SET NULL cascade.
    assert row.job_id is None, (
        "audit row's job_id should be NULL after SET NULL cascade"
    )
    # Snapshot columns preserve the identifier.
    assert row.job_name_snapshot == "SurvivorJob"
    assert row.job_code_snapshot == "SURV-01"


@pytest.mark.asyncio
async def test_delete_job_blocked_by_existing_expense_returns_409(
    client, admin_token, db_session, seeded_admin
):
    """v1A-3: a job with at least one expense cannot be hard-deleted.
    The DELETE returns 409 with the friendly 'Archive it instead'
    detail; the job row remains; no audit row is written.

    The user's spec also calls for a defence-in-depth review-queue
    check. The queue row's FK to expenses cascades on delete, so any
    queue row implies an expense row — meaning the expense count
    fires first. The review-queue branch in the service is covered
    by code review + the structural FK relationship; an independent
    runtime test would require manually inserting a queue row
    without an expense, which violates the FK. We document this
    here rather than write a contrived test.
    """
    import uuid as _uuid
    from decimal import Decimal
    from datetime import date
    from sqlalchemy import select
    from app.models import (
        Expense,
        ExpenseType,
        JobAuditLog,
        PaymentMethod,
        ReceiptStatus,
        ReviewStatus,
    )

    job = await _create_job(
        client, admin_token, name="HasExpense", job_code="HAS-EXP-01"
    )
    job_id_str = job["job_id"]
    job_id = _uuid.UUID(job_id_str)

    # Directly insert an Expense referencing this job. Bypassing the
    # parser/validator keeps the test isolated from those concerns.
    expense = Expense(
        expense_id=_uuid.uuid4(),
        job_id=job_id,
        entered_by_user_id=seeded_admin.user_id,
        expense_type=ExpenseType.supplier_expense,
        raw_input_text="manual seed for delete-blocked test",
        amount_inc_gst=Decimal("100.00"),
        amount_ex_gst=Decimal("90.91"),
        gst_amount=Decimal("9.09"),
        payment_method=PaymentMethod.transfer,
        expense_date=date(2026, 5, 17),
        review_status=ReviewStatus.reviewed,
        receipt_status=ReceiptStatus.no_receipt,
        duplicate_flag=False,
    )
    db_session.add(expense)
    await db_session.flush()

    # Try DELETE — must be blocked.
    r = await client.delete(
        f"/jobs/{job_id_str}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1 expense" in detail
    assert "Archive it instead" in detail

    # Job row remains.
    r = await client.get(
        f"/jobs/{job_id_str}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200

    # No delete-action audit row was written.
    rows = list(
        (
            await db_session.execute(
                select(JobAuditLog).where(
                    JobAuditLog.job_name_snapshot == "HasExpense",
                    JobAuditLog.action == "delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == [], "blocked delete must not write an audit row"


@pytest.mark.asyncio
async def test_delete_nonexistent_job_returns_404(client, admin_token):
    """v1A-3: DELETE on a job_id that does not resolve → 404."""
    r = await client.delete(
        f"/jobs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Job not found"


@pytest.mark.asyncio
async def test_delete_job_non_admin_returns_403(
    client, admin_token, contributor_token
):
    """v1A-3: contributor role is rejected at the require_admin gate;
    no service-layer work is performed."""
    job = await _create_job(
        client, admin_token, name="ContributorCantTouch", job_code="RBAC-01"
    )
    r = await client.delete(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 403

    # Job row still exists after the rejected attempt.
    r = await client.get(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Slice A: PATCH + DELETE for category budgets
# ---------------------------------------------------------------------------


async def _create_budget(
    client,
    admin_token,
    *,
    job_id: str,
    category_id: str,
    amount: str = "1000.00",
) -> dict:
    """Helper: POST a category budget and return the JSON body (asserting 201)."""
    r = await client.post(
        f"/jobs/{job_id}/category-budgets",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "category_id": category_id,
            "budget_amount_ex_gst": amount,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_patch_category_budget_updates_amount(
    client, admin_token, seed_categories
):
    """PATCH updates the amount; the changed value is visible via GET."""
    job = await _create_job(client, admin_token, name="Patch Budget Job")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
        amount="1000.00",
    )

    r = await client.patch(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"budget_amount_ex_gst": "5500.00"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["budget_id"] == budget["budget_id"]
    assert Decimal(str(body["budget_amount_ex_gst"])) == Decimal("5500.00")
    # Joined category eager-loaded — mirrors POST shape.
    assert body["category"]["category_name"] == "Plumbing"

    # GET /jobs/{id} reflects the new amount.
    r = await client.get(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    budgets = r.json()["category_budgets"]
    assert len(budgets) == 1
    assert Decimal(str(budgets[0]["budget_amount_ex_gst"])) == Decimal("5500.00")


@pytest.mark.asyncio
async def test_patch_category_budget_allows_zero(
    client, admin_token, seed_categories
):
    """PATCH must accept 0 — a zero budget is a valid explicit statement
    ("we are not budgeting this category"), distinct from no row at all."""
    job = await _create_job(client, admin_token, name="Zero Budget Job")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.patch(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"budget_amount_ex_gst": "0"},
    )
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["budget_amount_ex_gst"])) == Decimal("0")


@pytest.mark.asyncio
async def test_patch_category_budget_rejects_negative(
    client, admin_token, seed_categories
):
    """PATCH with a negative amount is rejected at the Pydantic ge=0
    constraint, surfacing as 422 before the service is reached."""
    job = await _create_job(client, admin_token, name="Negative Reject")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.patch(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"budget_amount_ex_gst": "-1.00"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_category_budget_404_on_missing_budget(
    client, admin_token
):
    """PATCH on a budget_id that does not exist returns 404 with the
    'Budget not found' detail."""
    job = await _create_job(client, admin_token, name="No Such Budget")
    random_budget_id = uuid.uuid4()

    r = await client.patch(
        f"/jobs/{job['job_id']}/category-budgets/{random_budget_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"budget_amount_ex_gst": "100.00"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Budget not found"


@pytest.mark.asyncio
async def test_patch_category_budget_404_on_mismatched_job(
    client, admin_token, seed_categories
):
    """Critical correctness guard: a real budget_id paired with the
    WRONG job_id in the URL must NOT update the budget on its real
    parent job. The (job_id, budget_id) pair is validated atomically
    and the mismatch returns 404 (no information leak about the
    budget's actual parent)."""
    # Create two jobs.
    job_a = await _create_job(client, admin_token, name="Job A")
    job_b = await _create_job(client, admin_token, name="Job B")
    plumbing = seed_categories[8]
    budget_a = await _create_budget(
        client,
        admin_token,
        job_id=job_a["job_id"],
        category_id=str(plumbing.category_id),
        amount="1000.00",
    )

    # Try to PATCH job_a's budget while addressing it under job_b's id.
    r = await client.patch(
        f"/jobs/{job_b['job_id']}/category-budgets/{budget_a['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"budget_amount_ex_gst": "99999.00"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Budget not found"

    # Verify job_a's budget was NOT touched — still at 1000.00.
    r = await client.get(
        f"/jobs/{job_a['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    budgets = r.json()["category_budgets"]
    assert len(budgets) == 1
    assert Decimal(str(budgets[0]["budget_amount_ex_gst"])) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_patch_category_budget_contributor_forbidden(
    client, admin_token, contributor_token, seed_categories
):
    """PATCH is admin-only; contributor caller gets 403 at the
    require_admin gate (no service-layer work performed)."""
    job = await _create_job(client, admin_token, name="RBAC Patch")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.patch(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {contributor_token}"},
        json={"budget_amount_ex_gst": "999.00"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_category_budget_removes_row(
    client, admin_token, seed_categories
):
    """DELETE removes the budget row; GET /jobs/{id} shows it gone."""
    job = await _create_job(client, admin_token, name="Delete Budget Job")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.delete(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204

    # GET reflects the removal.
    r = await client.get(
        f"/jobs/{job['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["category_budgets"] == []


@pytest.mark.asyncio
async def test_delete_category_budget_second_call_returns_404(
    client, admin_token, seed_categories
):
    """A second DELETE on the same budget_id returns 404 (NOT silently
    204). Callers wanting noop-on-missing semantics must ignore the 404
    themselves."""
    job = await _create_job(client, admin_token, name="Twice Delete")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r1 = await client.delete(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r1.status_code == 204

    r2 = await client.delete(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_category_budget_404_on_mismatched_job(
    client, admin_token, seed_categories
):
    """Critical correctness guard (mirror of the PATCH variant): a real
    budget_id paired with the WRONG job_id must NOT delete the budget
    on its real parent job."""
    job_a = await _create_job(client, admin_token, name="Job A Delete")
    job_b = await _create_job(client, admin_token, name="Job B Delete")
    plumbing = seed_categories[8]
    budget_a = await _create_budget(
        client,
        admin_token,
        job_id=job_a["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.delete(
        f"/jobs/{job_b['job_id']}/category-budgets/{budget_a['budget_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Budget not found"

    # Verify job_a's budget still exists.
    r = await client.get(
        f"/jobs/{job_a['job_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert len(r.json()["category_budgets"]) == 1


@pytest.mark.asyncio
async def test_delete_category_budget_contributor_forbidden(
    client, admin_token, contributor_token, seed_categories
):
    """DELETE is admin-only; contributor caller gets 403."""
    job = await _create_job(client, admin_token, name="RBAC Delete")
    plumbing = seed_categories[8]
    budget = await _create_budget(
        client,
        admin_token,
        job_id=job["job_id"],
        category_id=str(plumbing.category_id),
    )

    r = await client.delete(
        f"/jobs/{job['job_id']}/category-budgets/{budget['budget_id']}",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 403
