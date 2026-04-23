"""Tests for the Task T-N ``/review-queue`` HTTP API.

Covers the four routes:

* ``GET /review-queue`` — list (admin-only, status filter, opened_at ordering).
* ``GET /review-queue/{id}`` — detail (admin-only, nested expense + duplicate_of).
* ``POST /review-queue/{id}/resolve`` — admin-only atomic resolve.
* ``POST /review-queue/{id}/reject`` — admin-only atomic reject.

Atomicity
---------
``test_resolve_rollback_on_error`` constructs an expense_patch whose
``supplier_id`` points at a non-existent supplier. The service raises
``ValueError`` which the API maps to 422. That exit path triggers
``get_db``'s rollback and NOTHING the resolve attempted should persist:
expense.review_status stays pending, queue.status stays open, no audit
row is written.
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
    JobStatus,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewStatus,
    Supplier,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


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


async def _mk_expense(
    db_session,
    *,
    job_id,
    entered_by_user_id,
    amount: str = "100",
    description: str = "seed",
    supplier_id=None,
    review_status: ReviewStatus = ReviewStatus.pending,
    expense_date: _datetime.date | None = None,
    duplicate_of_expense_id=None,
    duplicate_flag: bool = False,
) -> Expense:
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job_id,
        supplier_id=supplier_id,
        entered_by_user_id=entered_by_user_id,
        expense_type=ExpenseType.supplier_expense,
        description=description,
        amount_inc_gst=Decimal(amount),
        expense_date=expense_date or _datetime.date.today(),
        review_status=review_status,
        receipt_status=ReceiptStatus.no_receipt,
        duplicate_of_expense_id=duplicate_of_expense_id,
        duplicate_flag=duplicate_flag,
    )
    db_session.add(exp)
    await db_session.flush()
    return exp


async def _mk_queue(
    db_session,
    *,
    expense_id,
    reasons: list[ReviewReasonCode] | None = None,
    status: ReviewQueueStatus = ReviewQueueStatus.open,
    resolved_by_user_id=None,
    resolved_at=None,
    resolution_notes: str | None = None,
) -> ExpenseReviewQueue:
    queue = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense_id,
        review_reasons=reasons or [ReviewReasonCode.amount_uncertain],
        status=status,
        resolved_by_user_id=resolved_by_user_id,
        resolved_at=resolved_at,
        resolution_notes=resolution_notes,
    )
    db_session.add(queue)
    await db_session.flush()
    return queue


async def _audit_rows_for(db_session, expense_id) -> list[ExpenseAuditLog]:
    stmt = select(ExpenseAuditLog).where(ExpenseAuditLog.expense_id == expense_id)
    return list((await db_session.execute(stmt)).scalars().all())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(db_session, seeded_admin):
    """Seed a minimal world: one job + one supplier."""
    job = await _mk_job(db_session, seeded_admin, name="Kelly House", code="KH-01")
    sup = await _mk_supplier(db_session, name="Bunnings")
    return {"admin": seeded_admin, "job": job, "sup": sup}


# ---------------------------------------------------------------------------
# GET /review-queue — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_auth(client):
    r = await client.get("/review-queue")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_contributor_403(client, world, contributor_token):
    r = await client.get("/review-queue", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_admin_200_shows_open_only_by_default(client, db_session, world, admin_token):
    """Default GET returns only status=open rows."""
    open_exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="open row",
    )
    open_queue = await _mk_queue(
        db_session,
        expense_id=open_exp.expense_id,
        status=ReviewQueueStatus.open,
    )
    resolved_exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="resolved row",
    )
    await _mk_queue(
        db_session,
        expense_id=resolved_exp.expense_id,
        status=ReviewQueueStatus.resolved,
        resolved_by_user_id=world["admin"].user_id,
        resolved_at=_datetime.datetime.now(_datetime.UTC),
    )

    r = await client.get("/review-queue", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {item["review_id"] for item in body}
    assert str(open_queue.review_id) in ids
    for item in body:
        assert item["status"] == "open"


@pytest.mark.asyncio
async def test_list_admin_status_filter(client, db_session, world, admin_token):
    """``?status=resolved`` returns only resolved rows."""
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    resolved = await _mk_queue(
        db_session,
        expense_id=exp.expense_id,
        status=ReviewQueueStatus.resolved,
        resolved_by_user_id=world["admin"].user_id,
        resolved_at=_datetime.datetime.now(_datetime.UTC),
    )

    r = await client.get("/review-queue?status=resolved", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {item["review_id"] for item in body}
    assert str(resolved.review_id) in ids
    for item in body:
        assert item["status"] == "resolved"


# ---------------------------------------------------------------------------
# GET /review-queue/{id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail_contributor_403(client, db_session, world, contributor_token):
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    r = await client.get(f"/review-queue/{queue.review_id}", headers=_auth(contributor_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_detail_admin_200(client, db_session, world, admin_token):
    """Returns queue + nested expense; duplicate_of=None when unset."""
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        supplier_id=world["sup"].supplier_id,
        description="detail target",
    )
    queue = await _mk_queue(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.amount_uncertain, ReviewReasonCode.supplier_uncertain],
    )

    r = await client.get(f"/review-queue/{queue.review_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_id"] == str(queue.review_id)
    assert body["expense_id"] == str(exp.expense_id)
    assert body["expense"]["description"] == "detail target"
    assert body["expense"]["supplier"]["supplier_name"] == "Bunnings"
    assert body["duplicate_of"] is None
    assert set(body["review_reasons"]) == {"amount_uncertain", "supplier_uncertain"}


@pytest.mark.asyncio
async def test_get_detail_includes_duplicate_of(client, db_session, world, admin_token):
    """If the reviewed expense has ``duplicate_of_expense_id`` set, it's surfaced."""
    original = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="original",
        review_status=ReviewStatus.reviewed,
    )
    dupe = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="possible duplicate",
        duplicate_of_expense_id=original.expense_id,
        duplicate_flag=True,
    )
    queue = await _mk_queue(
        db_session,
        expense_id=dupe.expense_id,
        reasons=[ReviewReasonCode.duplicate_suspected],
    )

    r = await client.get(f"/review-queue/{queue.review_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate_of"] is not None
    assert body["duplicate_of"]["expense_id"] == str(original.expense_id)
    assert body["duplicate_of"]["description"] == "original"


@pytest.mark.asyncio
async def test_get_detail_404_on_missing(client, admin_token):
    r = await client.get(f"/review-queue/{uuid.uuid4()}", headers=_auth(admin_token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /review-queue/{id}/resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_contributor_403(client, db_session, world, contributor_token):
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(contributor_token),
        json={},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_resolve_admin_204_atomic_transition(client, db_session, world, admin_token):
    """Admin resolves a pending expense: all three writes commit together."""
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={"notes": "approved by admin"},
    )
    assert r.status_code == 204, r.text

    await db_session.refresh(exp)
    assert exp.review_status == ReviewStatus.reviewed

    await db_session.refresh(queue)
    assert queue.status == ReviewQueueStatus.resolved
    assert queue.resolved_by_user_id == world["admin"].user_id
    assert queue.resolved_at is not None
    assert queue.resolution_notes == "approved by admin"

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.reason == "approved by admin"
    assert "review_status" in audit.changed_fields
    assert audit.changed_fields["review_status"]["old"] == "pending"
    assert audit.changed_fields["review_status"]["new"] == "reviewed"
    assert audit.edited_by_user_id == world["admin"].user_id


@pytest.mark.asyncio
async def test_resolve_applies_expense_patch(client, db_session, world, admin_token):
    """Resolve with expense_patch sets supplier; audit captures both changes."""
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        description="needs supplier",
        review_status=ReviewStatus.pending,
    )
    queue = await _mk_queue(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.supplier_uncertain],
    )
    new_supplier = await _mk_supplier(db_session, name="Tradelink")

    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={
            "expense_patch": {"supplier_id": str(new_supplier.supplier_id)},
            "notes": "added supplier during review",
        },
    )
    assert r.status_code == 204, r.text

    await db_session.refresh(exp)
    assert exp.supplier_id == new_supplier.supplier_id
    assert exp.review_status == ReviewStatus.reviewed

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.reason == "added supplier during review"
    assert "review_status" in audit.changed_fields
    assert audit.changed_fields["review_status"]["new"] == "reviewed"
    assert "supplier_id" in audit.changed_fields
    assert audit.changed_fields["supplier_id"]["new"] == str(new_supplier.supplier_id)


@pytest.mark.asyncio
async def test_resolve_409_when_already_resolved(client, db_session, world, admin_token):
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    queue = await _mk_queue(
        db_session,
        expense_id=exp.expense_id,
        status=ReviewQueueStatus.resolved,
        resolved_by_user_id=world["admin"].user_id,
        resolved_at=_datetime.datetime.now(_datetime.UTC),
    )

    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_resolve_404_on_missing(client, admin_token):
    r = await client.post(
        f"/review-queue/{uuid.uuid4()}/resolve",
        headers=_auth(admin_token),
        json={},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /review-queue/{id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_contributor_403(client, db_session, world, contributor_token):
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    r = await client.post(
        f"/review-queue/{queue.review_id}/reject",
        headers=_auth(contributor_token),
        json={},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reject_admin_204_atomic_transition(client, db_session, world, admin_token):
    """Admin rejects: expense + queue + audit all commit together."""
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    r = await client.post(
        f"/review-queue/{queue.review_id}/reject",
        headers=_auth(admin_token),
        json={"notes": "bogus entry"},
    )
    assert r.status_code == 204, r.text

    await db_session.refresh(exp)
    assert exp.review_status == ReviewStatus.rejected

    await db_session.refresh(queue)
    assert queue.status == ReviewQueueStatus.rejected
    assert queue.resolved_by_user_id == world["admin"].user_id
    assert queue.resolved_at is not None
    assert queue.resolution_notes == "bogus entry"

    rows = await _audit_rows_for(db_session, exp.expense_id)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.reason == "bogus entry"
    assert audit.changed_fields["review_status"]["old"] == "pending"
    assert audit.changed_fields["review_status"]["new"] == "rejected"


@pytest.mark.asyncio
async def test_reject_409_when_already_closed(client, db_session, world, admin_token):
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )
    queue = await _mk_queue(
        db_session,
        expense_id=exp.expense_id,
        status=ReviewQueueStatus.rejected,
        resolved_by_user_id=world["admin"].user_id,
        resolved_at=_datetime.datetime.now(_datetime.UTC),
    )

    r = await client.post(
        f"/review-queue/{queue.review_id}/reject",
        headers=_auth(admin_token),
        json={},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_reject_404_on_missing(client, admin_token):
    r = await client.post(
        f"/review-queue/{uuid.uuid4()}/reject",
        headers=_auth(admin_token),
        json={},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Atomicity — rollback verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_rollback_on_error(client, db_session, world, admin_token):
    """A bad expense_patch FK should roll back every write.

    The service validates supplier/category FK references up front and
    raises :class:`ValueError`, which the API maps to 422. Because the
    whole request runs inside a single ``get_db`` transaction, the
    rollback path fires — the expense must remain pending, the queue
    row must remain open, and no audit row must have been written.
    """
    exp = await _mk_expense(
        db_session,
        job_id=world["job"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    queue = await _mk_queue(db_session, expense_id=exp.expense_id)

    # Snapshot pre-state.
    pre_expense_status = exp.review_status
    pre_queue_status = queue.status
    pre_audit_count = len(await _audit_rows_for(db_session, exp.expense_id))

    # A supplier_id that doesn't resolve to any Supplier row.
    bogus_supplier_id = uuid.uuid4()

    r = await client.post(
        f"/review-queue/{queue.review_id}/resolve",
        headers=_auth(admin_token),
        json={
            "expense_patch": {"supplier_id": str(bogus_supplier_id)},
            "notes": "should not persist",
        },
    )
    assert r.status_code == 422, r.text

    # Re-query by PK to confirm nothing persisted.
    await db_session.refresh(exp)
    await db_session.refresh(queue)
    assert exp.review_status == pre_expense_status == ReviewStatus.pending
    assert queue.status == pre_queue_status == ReviewQueueStatus.open
    assert queue.resolved_by_user_id is None
    assert queue.resolved_at is None
    assert queue.resolution_notes is None

    post_audit_count = len(await _audit_rows_for(db_session, exp.expense_id))
    assert post_audit_count == pre_audit_count
