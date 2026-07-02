"""Integration tests for the Phase 3 Lite ``/jobs`` summary endpoints.

Hits the real ASGI app with admin / contributor JWTs:

* ``GET /jobs`` — every row carries a ``summary`` field (admin token)
* ``GET /jobs/{id}/budget-summary`` — happy path with mixed-status
  expenses and multiple categories; 404 on unknown id; 403 for
  contributor; 401 with no token; Decimal precision (string-shaped
  body, exact 0.01 quantization).

Service-layer math edge cases live in
``backend/tests/test_budget_summary_service.py`` — the API tests stay
focused on auth, status codes, and wire shape.
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest

from app.models.expense import (
    Expense,
    ExpenseType,
    PaymentMethod,
    ReceiptStatus,
    ReviewStatus,
)
from app.models.job import Job, JobCategoryBudget, JobStatus


def _today_iso() -> str:
    return _datetime.date.today().isoformat()


async def _mk_job(
    db,
    admin,
    *,
    name: str = "Job",
    total_budget_ex_gst: Decimal | None = None,
    contract_value_ex_gst: Decimal | None = None,
    target_profit_ratio_pct: Decimal | None = None,
    warning_amber_pct: Decimal | None = None,
    warning_red_pct: Decimal | None = None,
) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        total_budget_ex_gst=total_budget_ex_gst,
        contract_value_ex_gst=contract_value_ex_gst,
        target_profit_ratio_pct=target_profit_ratio_pct,
        warning_amber_pct=warning_amber_pct,
        warning_red_pct=warning_red_pct,
        created_by=admin.user_id,
    )
    db.add(job)
    await db.flush()
    return job


async def _mk_expense(
    db,
    *,
    job: Job,
    admin,
    amount_inc_gst: Decimal,
    payment_method: PaymentMethod = PaymentMethod.transfer,
    review_status: ReviewStatus = ReviewStatus.reviewed,
    category_id: uuid.UUID | None = None,
) -> Expense:
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=admin.user_id,
        expense_type=ExpenseType.supplier_expense,
        amount_inc_gst=amount_inc_gst,
        payment_method=payment_method,
        expense_date=_datetime.date.today(),
        review_status=review_status,
        receipt_status=ReceiptStatus.no_receipt,
        category_id=category_id,
    )
    db.add(exp)
    await db.flush()
    return exp


async def _mk_budget(
    db,
    *,
    job: Job,
    category_id: uuid.UUID,
    budget_amount_ex_gst: Decimal,
) -> JobCategoryBudget:
    bud = JobCategoryBudget(
        budget_id=uuid.uuid4(),
        job_id=job.job_id,
        category_id=category_id,
        budget_amount_ex_gst=budget_amount_ex_gst,
    )
    db.add(bud)
    await db.flush()
    return bud


# ---------------------------------------------------------------------------
# GET /jobs (extended)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_carries_summary_per_row(
    client, db_session, seeded_admin, admin_token
):
    """Every row in ``GET /jobs`` has a populated ``summary`` field."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        name="Summary Probe",
        total_budget_ex_gst=Decimal("1000.00"),
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("110.00"),
        payment_method=PaymentMethod.transfer,
    )

    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    row = next(j for j in rows if j["job_name"] == "Summary Probe")

    assert "summary" in row
    s = row["summary"]
    # actual_ex from one transfer of 110 → 100.00 ex / 10.00 GST
    assert Decimal(str(s["actual_inc_gst"])) == Decimal("110.00")
    assert Decimal(str(s["actual_ex_gst"])) == Decimal("100.00")
    assert Decimal(str(s["gst_amount"])) == Decimal("10.00")
    assert Decimal(str(s["total_budget_ex_gst"])) == Decimal("1000.00")
    assert Decimal(str(s["remaining_ex_gst"])) == Decimal("900.00")
    assert Decimal(str(s["percent_consumed"])) == Decimal("10.00")
    assert s["overspend"] is False


@pytest.mark.asyncio
async def test_list_jobs_summary_zero_for_empty_job(
    client, db_session, seeded_admin, admin_token
):
    """Job with no expenses still has a summary — all-zero, never null."""
    await _mk_job(db_session, seeded_admin, name="No Expenses")
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    row = next(j for j in r.json() if j["job_name"] == "No Expenses")
    s = row["summary"]
    assert Decimal(str(s["actual_inc_gst"])) == Decimal("0.00")
    assert Decimal(str(s["actual_ex_gst"])) == Decimal("0.00")
    assert s["total_budget_ex_gst"] is None
    assert s["overspend"] is False


@pytest.mark.asyncio
async def test_list_jobs_summary_stripped_for_contributor(
    client, db_session, seeded_admin, contributor_token
):
    """Jobs money strip (supersedes the Phase 3 Lite decision).

    Contributors still see ``GET /jobs`` (Phase 1 RBAC unchanged), but
    the ``summary`` field is now nulled server-side for them: the
    operator's conservative money-visibility rule classifies the
    budget/spend aggregates as admin-only, and the strip skips
    ``summarize_jobs`` entirely for contributor callers. Admin
    behaviour is covered by test_list_jobs_summary_* above and
    test_jobs.py::test_list_jobs_keeps_money_for_admin.
    """
    await _mk_job(db_session, seeded_admin, name="Contrib View")
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {contributor_token}"}
    )
    assert r.status_code == 200
    row = next(j for j in r.json() if j["job_name"] == "Contrib View")
    assert row["summary"] is None


# ---------------------------------------------------------------------------
# GET /jobs/{id}/budget-summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_summary_happy_path(
    client, db_session, seeded_admin, seed_categories, admin_token
):
    cat_a = seed_categories[0]  # Demolition
    cat_b = seed_categories[1]  # Earthworks (no budget)
    job = await _mk_job(
        db_session,
        seeded_admin,
        name="Detail Probe",
        total_budget_ex_gst=Decimal("10000.00"),
    )
    await _mk_budget(
        db_session,
        job=job,
        category_id=cat_a.category_id,
        budget_amount_ex_gst=Decimal("5000.00"),
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("1100.00"),
        payment_method=PaymentMethod.transfer,
        category_id=cat_a.category_id,
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
        payment_method=PaymentMethod.cash,
        review_status=ReviewStatus.pending,
        category_id=cat_b.category_id,
    )

    r = await client.get(
        f"/jobs/{job.job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == str(job.job_id)
    assert Decimal(str(body["actual_inc_gst"])) == Decimal("1200.00")
    # transfer 1100 → 1000.00 ex, 100.00 gst ; cash 100 → 100.00 ex, 0 gst
    assert Decimal(str(body["actual_ex_gst"])) == Decimal("1100.00")
    assert Decimal(str(body["gst_amount"])) == Decimal("100.00")
    assert Decimal(str(body["total_budget_ex_gst"])) == Decimal("10000.00")
    assert Decimal(str(body["remaining_ex_gst"])) == Decimal("8900.00")
    assert Decimal(str(body["percent_consumed"])) == Decimal("11.00")
    assert body["overspend"] is False

    by_id = {row["category_id"]: row for row in body["categories"]}
    a = by_id[str(cat_a.category_id)]
    assert Decimal(str(a["actual_ex_gst"])) == Decimal("1000.00")
    assert Decimal(str(a["budget_ex_gst"])) == Decimal("5000.00")
    assert Decimal(str(a["remaining_ex_gst"])) == Decimal("4000.00")

    b = by_id[str(cat_b.category_id)]
    assert Decimal(str(b["actual_ex_gst"])) == Decimal("100.00")
    assert b["budget_ex_gst"] is None
    assert b["remaining_ex_gst"] is None


@pytest.mark.asyncio
async def test_budget_summary_404_on_unknown_job(client, admin_token):
    bogus = uuid.uuid4()
    r = await client.get(
        f"/jobs/{bogus}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_budget_summary_403_for_contributor(
    client, db_session, seeded_admin, contributor_token
):
    job = await _mk_job(db_session, seeded_admin, name="Forbidden")
    r = await client.get(
        f"/jobs/{job.job_id}/budget-summary",
        headers={"Authorization": f"Bearer {contributor_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_budget_summary_401_no_token(
    client, db_session, seeded_admin
):
    job = await _mk_job(db_session, seeded_admin, name="No Token")
    r = await client.get(f"/jobs/{job.job_id}/budget-summary")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_budget_summary_decimal_string_shape(
    client, db_session, seeded_admin, admin_token
):
    """Money fields come back as strings, exactly 0.01-quantized."""
    job = await _mk_job(
        db_session, seeded_admin, total_budget_ex_gst=Decimal("100.00")
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("33.33"),
        payment_method=PaymentMethod.transfer,
    )
    r = await client.get(
        f"/jobs/{job.job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    for field in (
        "actual_inc_gst",
        "actual_ex_gst",
        "gst_amount",
        "total_budget_ex_gst",
        "remaining_ex_gst",
        "percent_consumed",
    ):
        # Pydantic v2 default: Decimal serialises as a quoted string.
        assert isinstance(body[field], str), field
        # Two decimal places exactly.
        assert "." in body[field] and len(body[field].split(".")[-1]) == 2


# ===========================================================================
# Phase 3 Lite+ — API integration for stored + derived + effective fields
# ===========================================================================


@pytest.mark.asyncio
async def test_post_jobs_accepts_phase3liteplus_fields(client, admin_token):
    """POST /jobs persists target_profit + warning thresholds; round-trip wire shape."""
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "job_name": "Lite+ POST",
            "contract_value_ex_gst": "200000.00",
            "total_budget_ex_gst": "188000.00",
            "target_profit_ratio_pct": "15.00",
            "warning_amber_pct": "70.00",
            "warning_red_pct": "90.00",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(body["target_profit_ratio_pct"]) == Decimal("15.00")
    assert Decimal(body["warning_amber_pct"]) == Decimal("70.00")
    assert Decimal(body["warning_red_pct"]) == Decimal("90.00")


@pytest.mark.asyncio
async def test_post_jobs_target_at_100_returns_422(client, admin_token):
    """target_profit_ratio_pct = 100 must be rejected at Pydantic (422)."""
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Bad Target", "target_profit_ratio_pct": "100.00"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_negative_target_returns_422(client, admin_token):
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Bad Target Neg", "target_profit_ratio_pct": "-1.00"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_amber_geq_red_returns_422(client, admin_token):
    """amber >= red is a Pydantic cross-field violation (422)."""
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "job_name": "Amber GE Red",
            "warning_amber_pct": "90.00",
            "warning_red_pct": "80.00",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_red_zero_returns_422(client, admin_token):
    """red must be strictly positive (gt=0)."""
    r = await client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"job_name": "Red Zero", "warning_red_pct": "0.00"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_jobs_updates_phase3liteplus_fields(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Lite+ PATCH")
    r = await client.patch(
        f"/jobs/{job.job_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "target_profit_ratio_pct": "15.00",
            "warning_amber_pct": "70.00",
            "warning_red_pct": "90.00",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["target_profit_ratio_pct"]) == Decimal("15.00")
    assert Decimal(body["warning_amber_pct"]) == Decimal("70.00")
    assert Decimal(body["warning_red_pct"]) == Decimal("90.00")


@pytest.mark.asyncio
async def test_get_jobs_summary_carries_effective_thresholds_default(
    client, db_session, seeded_admin, admin_token
):
    """Job with NULL stored thresholds → summary carries defaults 80 / 100."""
    await _mk_job(db_session, seeded_admin, name="Default Thresholds")
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    row = next(j for j in r.json() if j["job_name"] == "Default Thresholds")
    # Stored values still NULL on JobPublic
    assert row["warning_amber_pct"] is None
    assert row["warning_red_pct"] is None
    # Effective values populated on the summary
    assert Decimal(row["summary"]["effective_warning_amber_pct"]) == Decimal("80.00")
    assert Decimal(row["summary"]["effective_warning_red_pct"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_get_jobs_summary_carries_effective_thresholds_override(
    client, db_session, seeded_admin, admin_token
):
    await _mk_job(
        db_session,
        seeded_admin,
        name="Override Thresholds",
        warning_amber_pct=Decimal("70.00"),
        warning_red_pct=Decimal("90.00"),
    )
    r = await client.get(
        "/jobs", headers={"Authorization": f"Bearer {admin_token}"}
    )
    row = next(j for j in r.json() if j["job_name"] == "Override Thresholds")
    # Stored values present on JobPublic
    assert Decimal(row["warning_amber_pct"]) == Decimal("70.00")
    assert Decimal(row["warning_red_pct"]) == Decimal("90.00")
    # Effective values match the stored override (no fallback applied)
    assert Decimal(row["summary"]["effective_warning_amber_pct"]) == Decimal("70.00")
    assert Decimal(row["summary"]["effective_warning_red_pct"]) == Decimal("90.00")


@pytest.mark.asyncio
async def test_get_budget_summary_carries_all_phase3liteplus_fields(
    client, db_session, seeded_admin, admin_token
):
    """The full envelope carries margin fields + effective thresholds."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        name="Full Envelope",
        contract_value_ex_gst=Decimal("200000.00"),
        total_budget_ex_gst=Decimal("188000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
    )
    r = await client.get(
        f"/jobs/{job.job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["target_profit_ratio_pct"]) == Decimal("15.00")
    assert Decimal(body["target_cost_limit_ex_gst"]) == Decimal("170000.00")
    assert Decimal(body["budgeted_profit_ex_gst"]) == Decimal("12000.00")
    assert Decimal(body["budgeted_profit_ratio_pct"]) == Decimal("6.00")
    assert Decimal(body["budget_delta_vs_target_cost_ex_gst"]) == Decimal("18000.00")
    assert Decimal(body["effective_warning_amber_pct"]) == Decimal("80.00")
    assert Decimal(body["effective_warning_red_pct"]) == Decimal("100.00")
    # Confirm the misleading "actual_profit_*" fields are NOT in the wire response.
    assert "actual_profit_ex_gst" not in body
    assert "actual_profit_ratio_pct" not in body


@pytest.mark.asyncio
async def test_get_budget_summary_null_margin_fields_when_inputs_missing(
    client, db_session, seeded_admin, admin_token
):
    """A job with no contract/target/budget gets null margin fields."""
    job = await _mk_job(db_session, seeded_admin, name="Bare Job")
    r = await client.get(
        f"/jobs/{job.job_id}/budget-summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = r.json()
    assert body["target_profit_ratio_pct"] is None
    assert body["target_cost_limit_ex_gst"] is None
    assert body["budgeted_profit_ex_gst"] is None
    assert body["budgeted_profit_ratio_pct"] is None
    assert body["budget_delta_vs_target_cost_ex_gst"] is None
    # Effective thresholds always populated.
    assert body["effective_warning_amber_pct"] == "80.00"
    assert body["effective_warning_red_pct"] == "100.00"
