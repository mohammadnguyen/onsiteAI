"""Phase 2 Task T-B: model-level tests for :class:`Expense`.

Mirrors the style of ``tests/test_job_model.py``. Exercises:

* the ``before_insert`` GST-split listener (auto-compute mode)
* the same listener honouring explicit ex-GST / GST overrides
* the Postgres server defaults on all four enum columns
* the ``receipt_status`` enum's restricted two-value set (Phase 2 has
  no ``attached`` value; that is a Phase 5 addition)
* the self-referential ``duplicate_of_expense_id`` FK
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models import (
    Expense,
    ExpenseType,
    Job,
    JobStatus,
    PaymentMethod,
    ReceiptStatus,
    ReviewStatus,
)


async def _make_job(db_session, admin, *, name: str = "Kelly House") -> Job:
    """Fresh Job in the current transaction; mirrors test_job_model._make_job."""
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest.mark.asyncio
async def test_expense_roundtrip_with_computed_gst(db_session, seeded_admin):
    """Passing only ``amount_inc_gst`` auto-fills ex-GST + GST on flush."""
    job = await _make_job(db_session, seeded_admin)

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("110.00"),
        expense_date=date(2026, 4, 21),
    )
    db_session.add(expense)
    await db_session.flush()
    await db_session.refresh(expense)

    assert expense.amount_inc_gst == Decimal("110.00")
    assert expense.amount_ex_gst == Decimal("100.00")
    assert expense.gst_amount == Decimal("10.00")


@pytest.mark.asyncio
async def test_cash_is_gst_exclusive(db_session, seeded_admin):
    """Cash payments are GST-exclusive: ex == inc, gst == 0."""
    job = await _make_job(db_session, seeded_admin)

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("500.00"),
        payment_method=PaymentMethod.cash,
        expense_date=date(2026, 4, 24),
    )
    db_session.add(expense)
    await db_session.flush()
    await db_session.refresh(expense)

    # The "inc_gst" column name is legacy; for cash the stored value is
    # the total paid and is ALSO the ex-GST figure. The split listener
    # honors the cash rule documented on compute_gst_split.
    assert expense.amount_inc_gst == Decimal("500.00")
    assert expense.amount_ex_gst == Decimal("500.00")
    assert expense.gst_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_transfer_uses_standard_split(db_session, seeded_admin):
    """Transfer and unknown both use the standard 1/11 split."""
    job = await _make_job(db_session, seeded_admin)

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("1100.00"),
        payment_method=PaymentMethod.transfer,
        expense_date=date(2026, 4, 24),
    )
    db_session.add(expense)
    await db_session.flush()
    await db_session.refresh(expense)

    assert expense.amount_inc_gst == Decimal("1100.00")
    assert expense.amount_ex_gst == Decimal("1000.00")
    assert expense.gst_amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_expense_roundtrip_with_overridden_gst(db_session, seeded_admin):
    """If the caller sets all three amounts, the listener leaves them alone."""
    job = await _make_job(db_session, seeded_admin)

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        # Deliberately unusual split: 110 inc but only 5 of that is GST
        # (as might appear on a structured-entry adjustment).
        amount_inc_gst=Decimal("110.00"),
        amount_ex_gst=Decimal("105.00"),
        gst_amount=Decimal("5.00"),
        expense_date=date(2026, 4, 21),
    )
    db_session.add(expense)
    await db_session.flush()
    await db_session.refresh(expense)

    assert expense.amount_inc_gst == Decimal("110.00")
    assert expense.amount_ex_gst == Decimal("105.00")
    assert expense.gst_amount == Decimal("5.00")


@pytest.mark.asyncio
async def test_expense_defaults(db_session, seeded_admin):
    """Minimal insert takes the four enum + duplicate_flag server defaults."""
    job = await _make_job(db_session, seeded_admin)

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("110.00"),
        expense_date=date(2026, 4, 21),
    )
    db_session.add(expense)
    await db_session.flush()
    # Expire so the server defaults are loaded from DB rather than from
    # whatever Python-side defaults SQLAlchemy populated pre-flush.
    await db_session.refresh(expense)

    assert expense.expense_type == ExpenseType.supplier_expense
    assert expense.payment_method == PaymentMethod.unknown
    assert expense.review_status == ReviewStatus.pending
    assert expense.receipt_status == ReceiptStatus.no_receipt
    assert expense.duplicate_flag is False


@pytest.mark.asyncio
async def test_receipt_status_values_are_only_no_receipt_and_expected_later(
    db_session, seeded_admin
):
    """Phase 2's ``receipt_status`` enum does not include ``attached``.

    A raw SQL insert that attempts to use that value must fail — proof
    that the enum type really is restricted to ``no_receipt`` and
    ``expected_later`` (Phase 5 will extend it later).
    """
    job = await _make_job(db_session, seeded_admin)

    # Raw SQL so we bypass the Python enum coercion and reach Postgres
    # with an invalid literal. Using a SAVEPOINT so the expected
    # DBAPIError only poisons the inner block.
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                sa.text(
                    """
                    INSERT INTO expenses (
                        expense_id, job_id, entered_by_user_id,
                        amount_inc_gst, amount_ex_gst, gst_amount,
                        expense_date, receipt_status
                    ) VALUES (
                        :expense_id, :job_id, :user_id,
                        110.00, 100.00, 10.00,
                        :expense_date, 'attached'
                    )
                    """
                ),
                {
                    "expense_id": uuid.uuid4(),
                    "job_id": job.job_id,
                    "user_id": seeded_admin.user_id,
                    "expense_date": date(2026, 4, 21),
                },
            )


@pytest.mark.asyncio
async def test_self_fk_duplicate_of(db_session, seeded_admin):
    """A second expense can point back at the first via ``duplicate_of_expense_id``."""
    job = await _make_job(db_session, seeded_admin)

    expense1 = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("110.00"),
        expense_date=date(2026, 4, 21),
    )
    db_session.add(expense1)
    await db_session.flush()

    expense2 = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=seeded_admin.user_id,
        amount_inc_gst=Decimal("110.00"),
        expense_date=date(2026, 4, 21),
        duplicate_flag=True,
        duplicate_of_expense_id=expense1.expense_id,
    )
    db_session.add(expense2)
    await db_session.flush()

    # Reload expense2 with the ``duplicate_of`` relationship eagerly
    # available so the assertion does not trigger a lazy I/O.
    stmt = (
        select(Expense)
        .where(Expense.expense_id == expense2.expense_id)
    )
    reloaded = (await db_session.execute(stmt)).scalar_one()
    await db_session.refresh(reloaded, ["duplicate_of"])

    assert reloaded.duplicate_flag is True
    assert reloaded.duplicate_of is not None
    assert reloaded.duplicate_of.expense_id == expense1.expense_id
