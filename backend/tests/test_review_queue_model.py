"""Phase 2 Task T-C: model-level tests for the review queue + audit log.

Mirrors the style of ``tests/test_expense_model.py``. Exercises:

* round-tripping a ``review_reason_code[]`` array with multiple values
  (order preserved)
* the ``UNIQUE`` constraint on ``expense_id`` (one open review per
  expense)
* the CHECK that rejects an empty ``review_reasons`` array
* JSONB round-trip for ``expense_audit_log.changed_fields``
* the ``ON DELETE CASCADE`` from ``expenses`` to ``expense_audit_log``
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Expense,
    ExpenseAuditLog,
    ExpenseReviewQueue,
    Job,
    JobStatus,
    ReviewReasonCode,
)


async def _make_job(db_session, admin, *, name: str = "Kelly House") -> Job:
    """Fresh Job in the current transaction; mirrors test_expense_model._make_job."""
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _make_expense(db_session, job, admin) -> Expense:
    """Fresh Expense in the current transaction."""
    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=admin.user_id,
        amount_inc_gst=Decimal("110.00"),
        expense_date=date(2026, 4, 21),
    )
    db_session.add(expense)
    await db_session.flush()
    return expense


@pytest.mark.asyncio
async def test_review_queue_round_trip_with_multiple_reasons(
    db_session, seeded_admin
):
    """A ``review_reasons`` array of two enum values round-trips, order preserved."""
    job = await _make_job(db_session, seeded_admin)
    expense = await _make_expense(db_session, job, seeded_admin)

    entry = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        review_reasons=[
            ReviewReasonCode.amount_uncertain,
            ReviewReasonCode.supplier_uncertain,
        ],
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.refresh(entry)

    assert entry.review_reasons == [
        ReviewReasonCode.amount_uncertain,
        ReviewReasonCode.supplier_uncertain,
    ]


@pytest.mark.asyncio
async def test_review_queue_unique_expense_constraint(db_session, seeded_admin):
    """Only one queue row may exist per expense (UNIQUE on ``expense_id``)."""
    job = await _make_job(db_session, seeded_admin)
    expense = await _make_expense(db_session, job, seeded_admin)

    first = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        review_reasons=[ReviewReasonCode.job_uncertain],
    )
    db_session.add(first)
    await db_session.flush()

    # Attempt the duplicate insert via raw SQL inside a SAVEPOINT so the
    # IntegrityError is confined to the inner block and the outer
    # transaction stays healthy (keeps the fixture rollback clean).
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                sa.text(
                    """
                    INSERT INTO expense_review_queue
                        (review_id, expense_id, review_reasons)
                    VALUES
                        (:rid, :eid, ARRAY['category_uncertain']::review_reason_code[])
                    """
                ),
                {"rid": uuid.uuid4(), "eid": expense.expense_id},
            )


@pytest.mark.asyncio
async def test_review_queue_check_rejects_empty_array(db_session, seeded_admin):
    """The ``ck_..._non_empty`` CHECK rejects a zero-length reasons array."""
    job = await _make_job(db_session, seeded_admin)
    expense = await _make_expense(db_session, job, seeded_admin)

    # Raw SQL INSERT with an empty ``review_reason_code[]`` array so we
    # reach Postgres with the exact shape the CHECK must reject. Wrapped
    # in a SAVEPOINT so the IntegrityError stays local.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                sa.text(
                    """
                    INSERT INTO expense_review_queue
                        (review_id, expense_id, review_reasons)
                    VALUES
                        (:rid, :eid, ARRAY[]::review_reason_code[])
                    """
                ),
                {"rid": uuid.uuid4(), "eid": expense.expense_id},
            )


@pytest.mark.asyncio
async def test_audit_log_round_trip(db_session, seeded_admin):
    """JSONB ``changed_fields`` and optional ``reason`` round-trip cleanly."""
    job = await _make_job(db_session, seeded_admin)
    expense = await _make_expense(db_session, job, seeded_admin)

    entry = ExpenseAuditLog(
        audit_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        edited_by_user_id=seeded_admin.user_id,
        changed_fields={
            "amount_inc_gst": {"old": "100.00", "new": "110.00"},
        },
        reason="corrected by admin",
    )
    db_session.add(entry)
    await db_session.flush()

    stmt = select(ExpenseAuditLog).where(ExpenseAuditLog.audit_id == entry.audit_id)
    reloaded = (await db_session.execute(stmt)).scalar_one()

    assert reloaded.changed_fields == {
        "amount_inc_gst": {"old": "100.00", "new": "110.00"},
    }
    assert reloaded.reason == "corrected by admin"


@pytest.mark.asyncio
async def test_audit_log_cascade_on_expense_delete(db_session, seeded_admin):
    """Deleting the parent ``expenses`` row cascades to the audit log."""
    job = await _make_job(db_session, seeded_admin)
    expense = await _make_expense(db_session, job, seeded_admin)

    audit = ExpenseAuditLog(
        audit_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        edited_by_user_id=seeded_admin.user_id,
        changed_fields={"description": {"old": None, "new": "added note"}},
    )
    db_session.add(audit)
    await db_session.flush()
    audit_id = audit.audit_id

    # Delete the parent via raw SQL so ORM-level cascades are out of the
    # picture — we are asserting the Postgres ON DELETE CASCADE.
    await db_session.execute(
        sa.text("DELETE FROM expenses WHERE expense_id = :eid"),
        {"eid": expense.expense_id},
    )
    await db_session.flush()

    remaining = (
        await db_session.execute(
            sa.text(
                "SELECT audit_id FROM expense_audit_log WHERE audit_id = :aid"
            ),
            {"aid": audit_id},
        )
    ).first()
    assert remaining is None
