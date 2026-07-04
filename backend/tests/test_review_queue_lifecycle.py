"""Audit D-6 / T-2 — review-queue partial-open-unique lifecycle.

The one-open-row constraint is a partial unique index (WHERE status='open'),
so:

* an expense whose queue row was resolved can be RE-QUEUED with a fresh open
  row (the old one-row-for-all-time UNIQUE made this a hard IntegrityError/500);
* but two SIMULTANEOUSLY-open rows for one expense are still rejected.
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Expense,
    ExpenseReviewQueue,
    ExpenseType,
    Job,
    JobStatus,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewStatus,
)


async def _job_and_expense(db_session, admin):
    job = Job(
        job_id=uuid.uuid4(),
        job_code=f"RQ-{uuid.uuid4().hex[:6]}",
        job_name="Requeue Job",
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=admin.user_id,
        expense_type=ExpenseType.supplier_expense,
        description="requeue",
        amount_inc_gst=Decimal("110.00"),
        expense_date=_datetime.date.today(),
        review_status=ReviewStatus.pending,
        receipt_status=ReceiptStatus.no_receipt,
    )
    db_session.add(exp)
    await db_session.flush()
    return exp


def _open_row(expense_id) -> ExpenseReviewQueue:
    return ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense_id,
        review_reasons=[ReviewReasonCode.amount_uncertain],
        status=ReviewQueueStatus.open,
    )


@pytest.mark.asyncio
async def test_can_requeue_after_resolve(db_session, seeded_admin):
    """After the open row is resolved, a fresh open row can be inserted (T-2)."""
    exp = await _job_and_expense(db_session, seeded_admin)

    first = _open_row(exp.expense_id)
    db_session.add(first)
    await db_session.flush()

    # Resolve it (close the open row).
    first.status = ReviewQueueStatus.resolved
    first.resolved_at = _datetime.datetime.now(_datetime.UTC)
    await db_session.flush()

    # Re-queue: a NEW open row for the SAME expense — previously an
    # IntegrityError on the full unique constraint; now allowed.
    second = _open_row(exp.expense_id)
    db_session.add(second)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(ExpenseReviewQueue).where(
                ExpenseReviewQueue.expense_id == exp.expense_id
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    statuses = {r.status for r in rows}
    assert statuses == {ReviewQueueStatus.resolved, ReviewQueueStatus.open}


@pytest.mark.asyncio
async def test_two_open_rows_rejected(db_session, seeded_admin):
    """Two simultaneously-open rows for one expense are still rejected (D-6)."""
    exp = await _job_and_expense(db_session, seeded_admin)

    db_session.add(_open_row(exp.expense_id))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_open_row(exp.expense_id))
            await db_session.flush()
