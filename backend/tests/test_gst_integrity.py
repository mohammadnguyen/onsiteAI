"""Regression tests for the GST / money-integrity audit fixes.

Covers audit findings:

* B-1 — structured create with an inconsistent ``(ex, gst)`` pair is rejected.
* B-2 — a lone-component PATCH re-derives its sibling (triple stays consistent).
* B-3 — a cash expense with an explicit GST is forced GST-exclusive.
* X-1 — the reviewer-resolve path is payment-aware (cash keeps GST = 0).
* X-2 — the reviewer-resolve path re-derives a lone-component patch.
* T-1 / B-4 — DB CHECK constraints reject an inconsistent triple and negative
  money regardless of the write path.
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Expense,
    ExpenseReviewQueue,
    ExpenseType,
    Job,
    JobCategoryBudget,
    JobStatus,
    PaymentMethod,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewStatus,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _today_iso() -> str:
    return _datetime.date.today().isoformat()


async def _mk_job(db_session, admin) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_code=f"J-{uuid.uuid4().hex[:6]}",
        job_name="GST Test Job",
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_expense(
    db_session,
    *,
    job_id,
    entered_by_user_id,
    amount: str,
    payment_method: PaymentMethod = PaymentMethod.unknown,
) -> Expense:
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job_id,
        entered_by_user_id=entered_by_user_id,
        expense_type=ExpenseType.supplier_expense,
        description="gst seed",
        amount_inc_gst=Decimal(amount),
        payment_method=payment_method,
        expense_date=_datetime.date.today(),
        review_status=ReviewStatus.pending,
        receipt_status=ReceiptStatus.no_receipt,
    )
    db_session.add(exp)
    await db_session.flush()
    return exp


async def _mk_open_queue(db_session, expense_id) -> ExpenseReviewQueue:
    queue = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense_id,
        review_reasons=[ReviewReasonCode.amount_uncertain],
        status=ReviewQueueStatus.open,
    )
    db_session.add(queue)
    await db_session.flush()
    return queue


# ---------------------------------------------------------------------------
# B-1 — structured create both-supplied validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_inconsistent_gst_pair_rejected(client, db_session, seeded_admin, admin_token):
    """POST inc=110, ex=50, gst=50 (sum 100 != 110) -> 422, nothing persisted."""
    job = await _mk_job(db_session, seeded_admin)
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "amount_inc_gst": "110",
            "amount_ex_gst": "50",
            "gst_amount": "50",
            "expense_date": _today_iso(),
            "description": "inconsistent triple",
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_consistent_gst_pair_preserved(client, db_session, seeded_admin, admin_token):
    """POST inc=110, ex=105, gst=5 (a legitimate mixed-GST receipt) -> 201, preserved."""
    job = await _mk_job(db_session, seeded_admin)
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "amount_inc_gst": "110",
            "amount_ex_gst": "105",
            "gst_amount": "5",
            "expense_date": _today_iso(),
            "description": "mixed gst receipt",
        },
    )
    assert r.status_code == 201, r.text
    exp = r.json()["expense"]
    assert Decimal(exp["amount_ex_gst"]) == Decimal("105.00")
    assert Decimal(exp["gst_amount"]) == Decimal("5.00")


# ---------------------------------------------------------------------------
# B-3 — cash override forced GST-exclusive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_cash_with_explicit_gst_is_zeroed(
    client, db_session, seeded_admin, admin_token
):
    """POST cash inc=100 with an explicit gst=9.09 -> gst forced to 0.00, ex=100."""
    job = await _mk_job(db_session, seeded_admin)
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(job.job_id),
            "amount_inc_gst": "100",
            "gst_amount": "9.09",
            "payment_method": "cash",
            "expense_date": _today_iso(),
            "description": "cash with bogus gst",
        },
    )
    assert r.status_code == 201, r.text
    exp = r.json()["expense"]
    assert Decimal(exp["amount_ex_gst"]) == Decimal("100.00")
    assert Decimal(exp["gst_amount"]) == Decimal("0.00")


# ---------------------------------------------------------------------------
# B-2 — lone-component PATCH re-derives its sibling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_lone_gst_amount_rederives_ex(client, db_session, seeded_admin, admin_token):
    """PATCH only gst_amount=30 on inc=110 -> ex re-derived to 80 (sum stays 110)."""
    job = await _mk_job(db_session, seeded_admin)
    exp = await _mk_expense(
        db_session, job_id=job.job_id, entered_by_user_id=seeded_admin.user_id, amount="110"
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"gst_amount": "30"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["gst_amount"]) == Decimal("30.00")
    assert Decimal(body["amount_ex_gst"]) == Decimal("80.00")
    assert (
        Decimal(body["amount_ex_gst"]) + Decimal(body["gst_amount"])
        == Decimal(body["amount_inc_gst"])
    )


@pytest.mark.asyncio
async def test_patch_lone_amount_ex_gst_rederives_gst(
    client, db_session, seeded_admin, admin_token
):
    """PATCH only amount_ex_gst=70 on inc=110 -> gst re-derived to 40."""
    job = await _mk_job(db_session, seeded_admin)
    exp = await _mk_expense(
        db_session, job_id=job.job_id, entered_by_user_id=seeded_admin.user_id, amount="110"
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"amount_ex_gst": "70"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["amount_ex_gst"]) == Decimal("70.00")
    assert Decimal(body["gst_amount"]) == Decimal("40.00")


@pytest.mark.asyncio
async def test_patch_inconsistent_gst_pair_rejected(client, db_session, seeded_admin, admin_token):
    """PATCH ex=70 AND gst=50 on inc=110 (sum 120) -> 422."""
    job = await _mk_job(db_session, seeded_admin)
    exp = await _mk_expense(
        db_session, job_id=job.job_id, entered_by_user_id=seeded_admin.user_id, amount="110"
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"amount_ex_gst": "70", "gst_amount": "50"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# X-1 / X-2 — reviewer-resolve path is payment-aware + reconciles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_cash_patch_amount_keeps_gst_zero(
    client, db_session, seeded_admin, admin_token
):
    """Resolve a cash expense, correcting amount to 200 -> stays GST-exclusive (X-1)."""
    job = await _mk_job(db_session, seeded_admin)
    exp = await _mk_expense(
        db_session,
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount="100",
        payment_method=PaymentMethod.cash,
    )
    queue = await _mk_open_queue(db_session, exp.expense_id)
    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={"expense_patch": {"amount_inc_gst": "200"}, "notes": "corrected"},
    )
    assert r.status_code == 204, r.text
    await db_session.refresh(exp)
    assert exp.amount_inc_gst == Decimal("200.00")
    assert exp.amount_ex_gst == Decimal("200.00")
    assert exp.gst_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_resolve_lone_gst_patch_rederives_ex(client, db_session, seeded_admin, admin_token):
    """Resolve patching only gst_amount=30 on inc=110 -> ex re-derived to 80 (X-2)."""
    job = await _mk_job(db_session, seeded_admin)
    exp = await _mk_expense(
        db_session, job_id=job.job_id, entered_by_user_id=seeded_admin.user_id, amount="110"
    )
    queue = await _mk_open_queue(db_session, exp.expense_id)
    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={"expense_patch": {"gst_amount": "30"}, "notes": "corrected gst"},
    )
    assert r.status_code == 204, r.text
    await db_session.refresh(exp)
    assert exp.gst_amount == Decimal("30.00")
    assert exp.amount_ex_gst == Decimal("80.00")
    assert exp.amount_ex_gst + exp.gst_amount == exp.amount_inc_gst


# ---------------------------------------------------------------------------
# T-1 / B-4 — DB CHECK backstops (bypass the service layer entirely)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_rejects_inconsistent_triple(db_session, seeded_admin):
    """A direct-SQL-shaped inconsistent triple is rejected by the DB CHECK (T-1)."""
    job = await _mk_job(db_session, seeded_admin)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            bad = Expense(
                expense_id=uuid.uuid4(),
                job_id=job.job_id,
                entered_by_user_id=seeded_admin.user_id,
                expense_type=ExpenseType.supplier_expense,
                description="bad triple",
                amount_inc_gst=Decimal("110.00"),
                # Bypass the reconcile: both components set + inconsistent.
                amount_ex_gst=Decimal("50.00"),
                gst_amount=Decimal("50.00"),
                payment_method=PaymentMethod.transfer,
                expense_date=_datetime.date.today(),
            )
            db_session.add(bad)
            await db_session.flush()


@pytest.mark.asyncio
async def test_db_check_rejects_negative_expense_amount(db_session, seeded_admin):
    """A negative expense amount is rejected by ck_expenses_amounts_nonneg (B-4)."""
    job = await _mk_job(db_session, seeded_admin)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            bad = Expense(
                expense_id=uuid.uuid4(),
                job_id=job.job_id,
                entered_by_user_id=seeded_admin.user_id,
                expense_type=ExpenseType.supplier_expense,
                description="negative",
                amount_inc_gst=Decimal("-100.00"),
                amount_ex_gst=Decimal("-100.00"),
                gst_amount=Decimal("0.00"),
                payment_method=PaymentMethod.cash,
                expense_date=_datetime.date.today(),
            )
            db_session.add(bad)
            await db_session.flush()


@pytest.mark.asyncio
async def test_db_check_rejects_negative_budget_amount(db_session, seeded_admin, seed_categories):
    """A negative category budget is rejected by the DB CHECK (B-4)."""
    job = await _mk_job(db_session, seeded_admin)
    category = seed_categories[0]
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            bad = JobCategoryBudget(
                budget_id=uuid.uuid4(),
                job_id=job.job_id,
                category_id=category.category_id,
                budget_amount_ex_gst=Decimal("-1000.00"),
            )
            db_session.add(bad)
            await db_session.flush()
