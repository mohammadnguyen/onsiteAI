"""Tests for the Phase 3 Lite ``budget_summary`` service.

Covers every edge case enumerated in ``docs/phase-3-lite-plan.md``
under "Backend unit tests (pure)":

* Empty job (no expenses, no category budgets)
* Reviewed + pending mixed (both included)
* Rejected rows excluded from totals
* All-rejected job (sums = 0)
* Cash-payment GST (gst_amount = 0; amount_ex == amount_inc)
* Mixed cash + transfer (gst_amount invariant)
* NULL ``total_budget_ex_gst`` (remaining/percent are None)
* Zero ``total_budget_ex_gst`` (treated as NULL — no divide-by-zero)
* Per-category split (budget-only / actual-only / both / neither)
* Category overspend math (==, +1c)
* Decimal precision (Decimal('0.01') quantization, no float drift)
* JobNotFound raised for unknown job_id

These tests exercise the service directly against the rolled-back
``db_session`` fixture (no HTTP) so they run fast and surface
service-layer bugs without API noise.
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
from app.services.budget_summary import summarize_job, summarize_jobs
from app.services.jobs import JobNotFound


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> _datetime.date:
    return _datetime.date.today()


async def _mk_job(
    db,
    admin,
    *,
    name: str = "Job",
    total_budget_ex_gst: Decimal | None = None,
) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        total_budget_ex_gst=total_budget_ex_gst,
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
    """Insert one Expense; the model's before_insert listener fills the GST split."""
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        entered_by_user_id=admin.user_id,
        expense_type=ExpenseType.supplier_expense,
        amount_inc_gst=amount_inc_gst,
        # Leave amount_ex_gst / gst_amount unset so the listener computes them
        # via the production rule. That keeps these tests honest about the
        # cash-vs-transfer split — see app/models/expense.py.
        payment_method=payment_method,
        expense_date=_today(),
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
# summarize_jobs — job-level aggregate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_jobs_empty_job_returns_zero_summary(
    db_session, seeded_admin
):
    """Job with no expenses comes back with all-zero actuals."""
    job = await _mk_job(db_session, seeded_admin)
    out = await summarize_jobs(db_session, job_ids=[job.job_id])
    s = out[job.job_id]
    assert s.actual_inc_gst == Decimal("0.00")
    assert s.actual_ex_gst == Decimal("0.00")
    assert s.gst_amount == Decimal("0.00")
    assert s.total_budget_ex_gst is None
    assert s.remaining_ex_gst is None
    assert s.percent_consumed is None
    assert s.overspend is False


@pytest.mark.asyncio
async def test_summarize_jobs_includes_pending_and_reviewed(
    db_session, seeded_admin
):
    """Both ``reviewed`` and ``pending`` count toward actual totals."""
    job = await _mk_job(db_session, seeded_admin)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
        review_status=ReviewStatus.reviewed,
        payment_method=PaymentMethod.transfer,
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("200.00"),
        review_status=ReviewStatus.pending,
        payment_method=PaymentMethod.transfer,
    )
    out = await summarize_jobs(db_session, job_ids=[job.job_id])
    s = out[job.job_id]
    assert s.actual_inc_gst == Decimal("300.00")


@pytest.mark.asyncio
async def test_summarize_jobs_excludes_rejected(db_session, seeded_admin):
    """Adding a rejected expense does not move any total."""
    job = await _mk_job(db_session, seeded_admin)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
        review_status=ReviewStatus.reviewed,
    )
    before = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("9999.00"),
        review_status=ReviewStatus.rejected,
    )
    after = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert before.actual_inc_gst == after.actual_inc_gst
    assert before.actual_ex_gst == after.actual_ex_gst
    assert before.gst_amount == after.gst_amount


@pytest.mark.asyncio
async def test_summarize_jobs_all_rejected_zero_totals(
    db_session, seeded_admin
):
    """A job whose every expense is rejected reports zero actuals.

    With a budget set, ``remaining_ex_gst`` therefore equals the full
    budget (no consumption).
    """
    job = await _mk_job(
        db_session, seeded_admin, total_budget_ex_gst=Decimal("1000.00")
    )
    for _ in range(3):
        await _mk_expense(
            db_session,
            job=job,
            admin=seeded_admin,
            amount_inc_gst=Decimal("100.00"),
            review_status=ReviewStatus.rejected,
        )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.actual_ex_gst == Decimal("0.00")
    assert s.remaining_ex_gst == Decimal("1000.00")
    assert s.percent_consumed == Decimal("0.00")
    assert s.overspend is False


@pytest.mark.asyncio
async def test_summarize_jobs_cash_only_zero_gst(db_session, seeded_admin):
    """Cash payments → ``gst_amount = 0``, ``amount_ex == amount_inc``.

    The model's pre-insert listener already enforces this per row;
    aggregation just sums whatever's in the columns.
    """
    job = await _mk_job(db_session, seeded_admin)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("950.00"),
        payment_method=PaymentMethod.cash,
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.actual_inc_gst == Decimal("950.00")
    assert s.actual_ex_gst == Decimal("950.00")
    assert s.gst_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_summarize_jobs_mixed_cash_and_transfer_invariant(
    db_session, seeded_admin
):
    """``actual_inc_gst − actual_ex_gst == gst_amount`` regardless of mix."""
    job = await _mk_job(db_session, seeded_admin)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("550.00"),
        payment_method=PaymentMethod.transfer,
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
        payment_method=PaymentMethod.cash,
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    # transfer 550 → ex=500.00, gst=50.00 ; cash 100 → ex=100.00, gst=0
    assert s.actual_inc_gst == Decimal("650.00")
    assert s.actual_ex_gst == Decimal("600.00")
    assert s.gst_amount == Decimal("50.00")
    assert s.actual_inc_gst - s.actual_ex_gst == s.gst_amount


@pytest.mark.asyncio
async def test_summarize_jobs_null_budget_returns_none_for_remaining(
    db_session, seeded_admin
):
    """NULL budget → ``remaining`` and ``percent`` are None; overspend is False."""
    job = await _mk_job(db_session, seeded_admin, total_budget_ex_gst=None)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.total_budget_ex_gst is None
    assert s.remaining_ex_gst is None
    assert s.percent_consumed is None
    assert s.overspend is False


@pytest.mark.asyncio
async def test_summarize_jobs_zero_budget_treated_as_null(
    db_session, seeded_admin
):
    """Zero budget collapses to the NULL case — no divide-by-zero."""
    job = await _mk_job(
        db_session, seeded_admin, total_budget_ex_gst=Decimal("0.00")
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.total_budget_ex_gst == Decimal("0.00")
    assert s.remaining_ex_gst is None
    assert s.percent_consumed is None
    assert s.overspend is False


@pytest.mark.asyncio
async def test_summarize_jobs_overspend_boundary(db_session, seeded_admin):
    """Exact-equal spend is not overspend; one-cent over flips to True."""
    job = await _mk_job(
        db_session, seeded_admin, total_budget_ex_gst=Decimal("100.00")
    )
    # Exactly 100.00 ex (transfer 110 → ex=100.00)
    e1 = await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("110.00"),
        payment_method=PaymentMethod.transfer,
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.actual_ex_gst == Decimal("100.00")
    assert s.overspend is False
    assert s.percent_consumed == Decimal("100.00")

    # Add 1 cent ex via a cash entry of $0.01
    e2 = await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("0.01"),
        payment_method=PaymentMethod.cash,
    )
    s2 = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s2.actual_ex_gst == Decimal("100.01")
    assert s2.overspend is True
    assert s2.remaining_ex_gst == Decimal("-0.01")
    # Keep the references alive so ruff doesn't flag them as unused — both
    # rows have to actually live in the DB for the aggregate to see them.
    assert e1.expense_id != e2.expense_id


@pytest.mark.asyncio
async def test_summarize_jobs_empty_list_short_circuits(db_session):
    """``job_ids=[]`` returns ``{}`` without hitting the DB IN clause."""
    out = await summarize_jobs(db_session, job_ids=[])
    assert out == {}


# ---------------------------------------------------------------------------
# summarize_job — per-category breakdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_job_unknown_id_raises_jobnotfound(db_session):
    with pytest.raises(JobNotFound):
        await summarize_job(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_summarize_job_empty_returns_empty_categories(
    db_session, seeded_admin
):
    job = await _mk_job(db_session, seeded_admin)
    s = await summarize_job(db_session, job.job_id)
    assert s.actual_inc_gst == Decimal("0.00")
    assert s.categories == []


@pytest.mark.asyncio
async def test_summarize_job_category_split_includes_all_three_classes(
    db_session, seeded_admin, seed_categories
):
    """Categories list rules:

    * budget + actual → row included with both numbers
    * actual only → row included; budget=None, remaining=None
    * budget only → row included; actual=0
    * neither → row omitted
    """
    cats = seed_categories
    cat_with_both = cats[0]   # Demolition — has budget + spend
    cat_actual_only = cats[1]  # Earthworks — spend, no budget
    cat_budget_only = cats[2]  # Concrete — budget, no spend
    cat_neither = cats[3]      # Brickwork — neither (must NOT appear)

    job = await _mk_job(
        db_session, seeded_admin, total_budget_ex_gst=Decimal("10000.00")
    )
    await _mk_budget(
        db_session,
        job=job,
        category_id=cat_with_both.category_id,
        budget_amount_ex_gst=Decimal("5000.00"),
    )
    await _mk_budget(
        db_session,
        job=job,
        category_id=cat_budget_only.category_id,
        budget_amount_ex_gst=Decimal("2000.00"),
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("1100.00"),
        payment_method=PaymentMethod.transfer,
        category_id=cat_with_both.category_id,
    )
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("330.00"),
        payment_method=PaymentMethod.transfer,
        category_id=cat_actual_only.category_id,
    )

    s = await summarize_job(db_session, job.job_id)
    by_id = {row.category_id: row for row in s.categories}

    assert cat_neither.category_id not in by_id  # neither omitted

    both = by_id[cat_with_both.category_id]
    assert both.actual_ex_gst == Decimal("1000.00")
    assert both.budget_ex_gst == Decimal("5000.00")
    assert both.remaining_ex_gst == Decimal("4000.00")
    assert both.overspend is False

    actual_only = by_id[cat_actual_only.category_id]
    assert actual_only.actual_ex_gst == Decimal("300.00")
    assert actual_only.budget_ex_gst is None
    assert actual_only.remaining_ex_gst is None
    assert actual_only.overspend is False

    budget_only = by_id[cat_budget_only.category_id]
    assert budget_only.actual_ex_gst == Decimal("0.00")
    assert budget_only.budget_ex_gst == Decimal("2000.00")
    assert budget_only.remaining_ex_gst == Decimal("2000.00")
    assert budget_only.overspend is False


@pytest.mark.asyncio
async def test_summarize_job_category_overspend_boundary(
    db_session, seeded_admin, seed_categories
):
    """Per-category: ``actual == budget`` is not overspend; +1c flips."""
    cat = seed_categories[0]
    job = await _mk_job(db_session, seeded_admin)
    await _mk_budget(
        db_session,
        job=job,
        category_id=cat.category_id,
        budget_amount_ex_gst=Decimal("100.00"),
    )
    # Actual 100.00 ex — equal to budget
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("110.00"),
        payment_method=PaymentMethod.transfer,
        category_id=cat.category_id,
    )
    s = await summarize_job(db_session, job.job_id)
    row = next(r for r in s.categories if r.category_id == cat.category_id)
    assert row.actual_ex_gst == Decimal("100.00")
    assert row.overspend is False

    # +1 cent
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("0.01"),
        payment_method=PaymentMethod.cash,
        category_id=cat.category_id,
    )
    s2 = await summarize_job(db_session, job.job_id)
    row2 = next(r for r in s2.categories if r.category_id == cat.category_id)
    assert row2.actual_ex_gst == Decimal("100.01")
    assert row2.overspend is True


@pytest.mark.asyncio
async def test_summarize_job_excludes_null_category_from_breakdown(
    db_session, seeded_admin
):
    """Expenses with ``category_id IS NULL`` count toward job totals only.

    They must not appear in the per-category breakdown — there is no
    category to attribute them to.
    """
    job = await _mk_job(db_session, seeded_admin)
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("100.00"),
        category_id=None,
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.actual_inc_gst == Decimal("100.00")
    assert s.categories == []


@pytest.mark.asyncio
async def test_summarize_job_decimal_precision_no_float_drift(
    db_session, seeded_admin
):
    """Combinations of cash + transfer rows quantize to exact 0.01."""
    job = await _mk_job(db_session, seeded_admin)
    # Three transfer rows whose 1/11 splits would each carry a 1/3-cent
    # rounding tail in float arithmetic. Decimal must avoid that.
    for amount in (Decimal("33.33"), Decimal("66.67"), Decimal("100.01")):
        await _mk_expense(
            db_session,
            job=job,
            admin=seeded_admin,
            amount_inc_gst=amount,
            payment_method=PaymentMethod.transfer,
        )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    # All summary money fields must be exactly 0.01-quantized
    for value in (s.actual_inc_gst, s.actual_ex_gst, s.gst_amount):
        # Decimal exponent of -2 means scale=2, i.e. quantized to 0.01
        assert value.as_tuple().exponent == -2, value
