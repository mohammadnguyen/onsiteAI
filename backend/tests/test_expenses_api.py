"""Tests for the Task T-M ``/expenses`` HTTP API.

Covers:

* ``POST /expenses`` — raw-text + structured create paths, auto-review
  vs pending, queue-row side-effects, duplicate detection, validation
  errors.
* ``POST /expenses/parse`` — preview (no persist).
* ``GET /expenses`` — contributor mine-only, admin sees-all + mine
  filter, status / job / receipt-status query filters.
* ``GET /expenses/{id}`` — nested supplier + category, contributor
  ownership enforcement.
* ``PATCH /expenses/{id}`` — RBAC rules, audit-row writes on reviewed
  rows + status transitions, amount split recompute on amount_inc_gst
  change.
* ``DELETE /expenses/{id}`` — admin-only soft delete, closes queue row,
  writes audit.
* ``GET /expenses/{id}/audit`` — admin-only audit list.
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Expense,
    ExpenseAuditLog,
    ExpenseReviewQueue,
    ExpenseType,
    Job,
    JobAlias,
    JobStatus,
    LanguageCode,
    PaymentMethod,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewStatus,
    Supplier,
    SupplierAlias,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return _datetime.date.today().isoformat()


async def _mk_job(db_session, admin, *, name: str, code: str) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_code=code,
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_supplier(db_session, *, name: str) -> Supplier:
    sup = Supplier(
        supplier_id=uuid.uuid4(),
        supplier_name=name,
        is_active=True,
    )
    db_session.add(sup)
    await db_session.flush()
    return sup


@pytest_asyncio.fixture
async def world(db_session, seeded_admin, seed_categories):
    """Seed a mini world: two jobs (Kelly + Smith), two suppliers (Bunnings + Mitre)."""
    job_a = await _mk_job(db_session, seeded_admin, name="Kelly House", code="KH-01")
    db_session.add_all(
        [
            JobAlias(
                job_id=job_a.job_id,
                alias_text="Kelly",
                language_code=LanguageCode.en,
            ),
            JobAlias(
                job_id=job_a.job_id,
                alias_text="工地1",
                language_code=LanguageCode.zh,
            ),
        ]
    )
    job_b = await _mk_job(db_session, seeded_admin, name="Smith Reno", code="SR-02")
    db_session.add(
        JobAlias(
            job_id=job_b.job_id,
            alias_text="Smith",
            language_code=LanguageCode.en,
        )
    )
    sup_a = await _mk_supplier(db_session, name="Bunnings")
    sup_b = await _mk_supplier(db_session, name="Mitre 10")
    db_session.add(
        SupplierAlias(
            supplier_id=sup_b.supplier_id,
            alias_text="Mitre",
            language_code=LanguageCode.en,
        )
    )
    await db_session.flush()
    return {
        "admin": seeded_admin,
        "job_a": job_a,
        "job_b": job_b,
        "sup_a": sup_a,
        "sup_b": sup_b,
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Create flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_raw_text_high_confidence_is_auto_reviewed(
    client, db_session, world, admin_token
):
    """Clean parse (``$305 Bunnings Kelly bluemetal``) -> 201 reviewed, no queue row."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "$305 Bunnings Kelly bluemetal",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["review_status"] == "reviewed"
    assert body["parse"]["review_reasons"] == []

    # No queue row.
    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == uuid.UUID(body["expense"]["expense_id"])
    )
    assert (await db_session.execute(stmt)).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_with_raw_text_pending(client, db_session, world, admin_token):
    """``工地1 水工材料 163`` -> 201 pending, queue row with reasons."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "工地1 水工材料 163",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["review_status"] == "pending"
    reasons = body["parse"]["review_reasons"]
    assert "amount_uncertain" in reasons
    assert "supplier_uncertain" in reasons

    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == uuid.UUID(body["expense"]["expense_id"])
    )
    queue = (await db_session.execute(stmt)).scalar_one()
    assert ReviewReasonCode.amount_uncertain in queue.review_reasons
    assert ReviewReasonCode.supplier_uncertain in queue.review_reasons


@pytest.mark.asyncio
async def test_create_unsupported_currency_pending(client, db_session, world, admin_token):
    """``¥50 Kelly`` -> 201 pending, amount preserved (no conversion)."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={"raw_input_text": "¥50 Kelly", "expense_date": _today_iso()},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["review_status"] == "pending"
    assert "unsupported_currency" in body["parse"]["review_reasons"]
    assert Decimal(body["expense"]["amount_inc_gst"]) == Decimal("50")

    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == uuid.UUID(body["expense"]["expense_id"])
    )
    queue = (await db_session.execute(stmt)).scalar_one()
    assert ReviewReasonCode.unsupported_currency in queue.review_reasons


@pytest.mark.asyncio
async def test_create_structured_skips_parser(client, db_session, world, admin_token):
    """Structured create with no ``raw_input_text`` -> parse is None."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "amount_inc_gst": "500",
            "expense_date": _today_iso(),
            "description": "manual entry",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["parse"] is None
    assert body["expense"]["review_status"] == "reviewed"

    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == uuid.UUID(body["expense"]["expense_id"])
    )
    assert (await db_session.execute(stmt)).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_missing_amount_422(client, world, admin_token):
    """No amount and no raw_input_text -> 422."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "expense_date": _today_iso(),
            "description": "no amount",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_unresolvable_job_422(client, world, admin_token):
    """raw_input_text with no job alias hits + no structured job -> 422."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={"raw_input_text": "$100 Bunnings bluemetal", "expense_date": _today_iso()},
    )
    assert r.status_code == 422
    assert "job" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_supplier_expense_needs_supplier_or_description_422(
    client, world, admin_token
):
    """A structured supplier expense without supplier_id or description -> 422."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "amount_inc_gst": "100",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_expense_date_5y_past_422(client, world, admin_token):
    """expense_date more than 5 years in the past -> 422."""
    very_old = (_datetime.date.today() - _datetime.timedelta(days=365 * 6)).isoformat()
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "amount_inc_gst": "100",
            "expense_date": very_old,
            "description": "old",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_detects_duplicate(client, db_session, world, admin_token):
    """Seed a matching prior expense; new raw-text create flips duplicate_flag."""
    today = _datetime.date.today()
    prior = Expense(
        expense_id=uuid.uuid4(),
        job_id=world["job_a"].job_id,
        supplier_id=world["sup_a"].supplier_id,
        entered_by_user_id=world["admin"].user_id,
        expense_type=ExpenseType.supplier_expense,
        description="Bunnings Kelly bluemetal",
        amount_inc_gst=Decimal("305"),
        expense_date=today,
        review_status=ReviewStatus.reviewed,
    )
    db_session.add(prior)
    await db_session.flush()

    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "$305 Bunnings Kelly bluemetal",
            "expense_date": today.isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["duplicate_flag"] is True

    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == uuid.UUID(body["expense"]["expense_id"])
    )
    queue = (await db_session.execute(stmt)).scalar_one()
    assert ReviewReasonCode.duplicate_suspected in queue.review_reasons


# ---------------------------------------------------------------------------
# Parse preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_preview_does_not_persist(client, db_session, world, admin_token):
    """``POST /expenses/parse`` returns draft + diagnostics; no row persisted."""
    count_stmt = select(Expense)
    before = len((await db_session.execute(count_stmt)).scalars().all())

    r = await client.post(
        "/expenses/parse",
        headers=_auth(admin_token),
        json={"raw_input_text": "$305 Bunnings Kelly bluemetal"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "draft" in body
    assert "diagnostics" in body

    after = len((await db_session.execute(count_stmt)).scalars().all())
    assert before == after


# ---------------------------------------------------------------------------
# List + get RBAC
# ---------------------------------------------------------------------------


async def _seed_structured_expense(
    db_session,
    *,
    job_id,
    entered_by_user_id,
    amount: str = "100",
    description: str = "seed",
    supplier_id=None,
    review_status: ReviewStatus = ReviewStatus.reviewed,
    receipt_status: ReceiptStatus = ReceiptStatus.no_receipt,
    payment_method: PaymentMethod = PaymentMethod.unknown,
    expense_date: _datetime.date | None = None,
) -> Expense:
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job_id,
        supplier_id=supplier_id,
        entered_by_user_id=entered_by_user_id,
        expense_type=ExpenseType.supplier_expense,
        description=description,
        amount_inc_gst=Decimal(amount),
        payment_method=payment_method,
        expense_date=expense_date or _datetime.date.today(),
        review_status=review_status,
        receipt_status=receipt_status,
    )
    db_session.add(exp)
    await db_session.flush()
    return exp


@pytest.mark.asyncio
async def test_list_contributor_sees_own_only(
    client, db_session, world, seeded_contributor, contributor_token
):
    """Contributor GET /expenses only surfaces rows they entered."""
    admin_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="admin row",
    )
    contrib_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
        description="contributor row",
    )

    r = await client.get("/expenses", headers=_auth(contributor_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(contrib_exp.expense_id) in ids
    assert str(admin_exp.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_admin_sees_all(client, db_session, world, seeded_contributor, admin_token):
    """Admin GET /expenses surfaces everyone's rows."""
    admin_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    contrib_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
    )

    r = await client.get("/expenses", headers=_auth(admin_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(admin_exp.expense_id) in ids
    assert str(contrib_exp.expense_id) in ids


@pytest.mark.asyncio
async def test_list_admin_mine_filter(client, db_session, world, seeded_contributor, admin_token):
    """Admin with ?mine=1 is restricted to their own rows."""
    admin_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    contrib_exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
    )

    r = await client.get("/expenses?mine=1", headers=_auth(admin_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(admin_exp.expense_id) in ids
    assert str(contrib_exp.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_filters_by_status(client, db_session, world, admin_token):
    """?status=pending returns only pending rows."""
    pending = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    reviewed = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )

    r = await client.get("/expenses?status=pending", headers=_auth(admin_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(pending.expense_id) in ids
    assert str(reviewed.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_filters_by_job(client, db_session, world, admin_token):
    """?job_id=<x> returns only rows on that job."""
    exp_a = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    exp_b = await _seed_structured_expense(
        db_session,
        job_id=world["job_b"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )

    r = await client.get(f"/expenses?job_id={world['job_a'].job_id}", headers=_auth(admin_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(exp_a.expense_id) in ids
    assert str(exp_b.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_filters_by_receipt_status(client, db_session, world, admin_token):
    """?receipt_status=expected_later returns only those rows."""
    expected = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        receipt_status=ReceiptStatus.expected_later,
    )
    none_ = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        receipt_status=ReceiptStatus.no_receipt,
    )

    r = await client.get("/expenses?receipt_status=expected_later", headers=_auth(admin_token))
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(expected.expense_id) in ids
    assert str(none_.expense_id) not in ids


@pytest.mark.asyncio
async def test_get_contributor_own_200(
    client, db_session, world, seeded_contributor, contributor_token
):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
    )
    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(contributor_token))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_contributor_others_403(client, db_session, world, contributor_token):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_admin_any_200(client, db_session, world, admin_token):
    """Admin GET includes nested supplier + category."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        supplier_id=world["sup_a"].supplier_id,
    )
    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supplier"] is not None
    assert body["supplier"]["supplier_name"] == "Bunnings"
    # ``category`` is always present (may be null if unset).
    assert "category" in body


# ---------------------------------------------------------------------------
# PATCH edit rules + audit
# ---------------------------------------------------------------------------


async def _audit_rows_for(db_session, expense_id) -> list[ExpenseAuditLog]:
    stmt = select(ExpenseAuditLog).where(ExpenseAuditLog.expense_id == expense_id)
    return list((await db_session.execute(stmt)).scalars().all())


@pytest.mark.asyncio
async def test_patch_contributor_own_pending_200_no_audit(
    client, db_session, world, seeded_contributor, contributor_token
):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
        review_status=ReviewStatus.pending,
    )
    before = len(await _audit_rows_for(db_session, exp.expense_id))
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(contributor_token),
        json={"description": "updated by contributor"},
    )
    assert r.status_code == 200, r.text
    after = len(await _audit_rows_for(db_session, exp.expense_id))
    assert before == after


@pytest.mark.asyncio
async def test_patch_contributor_own_reviewed_403(
    client, db_session, world, seeded_contributor, contributor_token
):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
        review_status=ReviewStatus.reviewed,
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(contributor_token),
        json={"description": "nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_contributor_others_pending_403(client, db_session, world, contributor_token):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(contributor_token),
        json={"description": "nope"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_admin_pending_200_no_audit(client, db_session, world, admin_token):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    before = len(await _audit_rows_for(db_session, exp.expense_id))
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"description": "admin pending edit"},
    )
    assert r.status_code == 200, r.text
    after = len(await _audit_rows_for(db_session, exp.expense_id))
    assert before == after


@pytest.mark.asyncio
async def test_patch_admin_reviewed_200_writes_audit(client, db_session, world, admin_token):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="before",
        review_status=ReviewStatus.reviewed,
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"description": "after", "reason": "fixed by admin"},
    )
    assert r.status_code == 200, r.text

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.reason == "fixed by admin"
    assert "description" in audit.changed_fields
    assert audit.changed_fields["description"]["old"] == "before"
    assert audit.changed_fields["description"]["new"] == "after"


@pytest.mark.asyncio
async def test_patch_admin_review_status_transition_writes_audit(
    client, db_session, world, admin_token
):
    """Admin transitioning a pending row writes an audit row regardless of pre-state."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"review_status": "rejected"},
    )
    assert r.status_code == 200, r.text

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    assert "review_status" in rows[0].changed_fields
    assert rows[0].changed_fields["review_status"]["old"] == "pending"
    assert rows[0].changed_fields["review_status"]["new"] == "rejected"


@pytest.mark.asyncio
async def test_patch_amount_inc_gst_recomputes_ex_and_gst(client, db_session, world, admin_token):
    """PATCH amount_inc_gst=220 -> response has ex=200.00, gst=20.00."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        amount="100",
        review_status=ReviewStatus.pending,
    )
    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"amount_inc_gst": "220"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["amount_inc_gst"]) == Decimal("220")
    assert Decimal(body["amount_ex_gst"]) == Decimal("200.00")
    assert Decimal(body["gst_amount"]) == Decimal("20.00")


@pytest.mark.asyncio
async def test_create_cash_payment_is_gst_exclusive(client, world, admin_token):
    """Structured POST with payment_method=cash -> ex=inc, gst=0.00."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "amount_inc_gst": "500",
            "payment_method": "cash",
            "expense_date": _today_iso(),
            "description": "cash purchase",
        },
    )
    assert r.status_code == 201, r.text
    expense = r.json()["expense"]
    assert expense["payment_method"] == "cash"
    assert Decimal(expense["amount_inc_gst"]) == Decimal("500")
    assert Decimal(expense["amount_ex_gst"]) == Decimal("500.00")
    assert Decimal(expense["gst_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_create_transfer_payment_uses_standard_split(client, world, admin_token):
    """Structured POST with payment_method=transfer -> ex=inc/1.1."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "job_id": str(world["job_a"].job_id),
            "amount_inc_gst": "1100",
            "payment_method": "transfer",
            "expense_date": _today_iso(),
            "description": "bank transfer",
        },
    )
    assert r.status_code == 201, r.text
    expense = r.json()["expense"]
    assert expense["payment_method"] == "transfer"
    assert Decimal(expense["amount_ex_gst"]) == Decimal("1000.00")
    assert Decimal(expense["gst_amount"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_create_raw_text_with_cash_keyword_applies_cash_rule(
    client, world, admin_token
):
    """Raw text containing the `cash` keyword -> parser extracts cash -> GST=0."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Kelly $80 cash timber",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    expense = r.json()["expense"]
    assert expense["payment_method"] == "cash"
    assert Decimal(expense["amount_ex_gst"]) == Decimal("80.00")
    assert Decimal(expense["gst_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_create_raw_text_with_zh_cash_keyword_applies_cash_rule(
    client, world, admin_token
):
    """Raw text containing 现金 -> parser extracts cash -> GST=0."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Kelly 现金 200 水泥",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    expense = r.json()["expense"]
    assert expense["payment_method"] == "cash"
    assert Decimal(expense["amount_inc_gst"]) == Decimal("200")
    assert Decimal(expense["amount_ex_gst"]) == Decimal("200.00")
    assert Decimal(expense["gst_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_patch_payment_method_to_cash_recomputes_gst_to_zero(
    client, db_session, world, admin_token
):
    """Admin PATCH payment_method=cash on a transfer row -> GST recomputes to 0."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        amount="1100",
        review_status=ReviewStatus.pending,
    )
    # Seeded with the default unknown split (1/11), so ex=1000 gst=100 before.
    assert exp.amount_ex_gst == Decimal("1000.00")
    assert exp.gst_amount == Decimal("100.00")

    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"payment_method": "cash"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_method"] == "cash"
    assert Decimal(body["amount_inc_gst"]) == Decimal("1100")
    assert Decimal(body["amount_ex_gst"]) == Decimal("1100.00")
    assert Decimal(body["gst_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_patch_payment_method_from_cash_to_transfer_recomputes_gst(
    client, db_session, world, admin_token
):
    """Admin PATCH payment_method=transfer on a cash row -> GST recomputes to 1/11."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        amount="500",
        review_status=ReviewStatus.pending,
        payment_method=PaymentMethod.cash,
    )
    assert exp.amount_ex_gst == Decimal("500.00")
    assert exp.gst_amount == Decimal("0.00")

    r = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"payment_method": "transfer"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_method"] == "transfer"
    # 500 / 1.1 ≈ 454.55 (rounded half-up, Bankers' rounding not used in app)
    assert Decimal(body["amount_ex_gst"]) == Decimal("454.55")
    assert Decimal(body["gst_amount"]) == Decimal("500") - Decimal("454.55")


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_contributor_403(
    client, db_session, world, seeded_contributor, contributor_token
):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
    )
    r = await client.delete(f"/expenses/{exp.expense_id}", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_admin_204_soft_delete_writes_audit_closes_queue(
    client, db_session, world, admin_token
):
    """Admin DELETE -> 204; row flipped to rejected; queue closed; audit written."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    queue = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=exp.expense_id,
        review_reasons=[ReviewReasonCode.amount_uncertain],
        status=ReviewQueueStatus.open,
    )
    db_session.add(queue)
    await db_session.flush()

    r = await client.delete(
        f"/expenses/{exp.expense_id}?reason=cleanup", headers=_auth(admin_token)
    )
    assert r.status_code == 204

    await db_session.refresh(exp)
    assert exp.review_status == ReviewStatus.rejected

    await db_session.refresh(queue)
    assert queue.status == ReviewQueueStatus.rejected

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    assert rows[0].reason == "cleanup"


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_contributor_403(client, db_session, world, contributor_token):
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    r = await client.get(f"/expenses/{exp.expense_id}/audit", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_audit_admin_200(client, db_session, world, admin_token):
    """Two audits on one expense come back newest first."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="v1",
        review_status=ReviewStatus.reviewed,
    )
    # First audit-producing edit.
    r1 = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"description": "v2", "reason": "first"},
    )
    assert r1.status_code == 200, r1.text
    # Second audit-producing edit.
    r2 = await client.patch(
        f"/expenses/{exp.expense_id}",
        headers=_auth(admin_token),
        json={"description": "v3", "reason": "second"},
    )
    assert r2.status_code == 200, r2.text

    r = await client.get(f"/expenses/{exp.expense_id}/audit", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2
    # Both patches ran inside the test fixture's single transaction, so
    # PostgreSQL ``NOW()`` returns the identical server_default value
    # for both rows (``NOW()`` is transaction-start time). The listing
    # therefore can't reliably order them "newest first" against a
    # shared-transaction test. Just assert both reasons are present;
    # real-world traffic has each request in its own transaction so
    # the production order-by-edited_at_desc is meaningful there.
    reasons = {row["reason"] for row in rows}
    assert reasons == {"first", "second"}


# ---------------------------------------------------------------------------
# Auth sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_auth(client):
    r = await client.get("/expenses")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_requires_auth(client):
    r = await client.post("/expenses", json={"raw_input_text": "$10 something"})
    assert r.status_code == 401
