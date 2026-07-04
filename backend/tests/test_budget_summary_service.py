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
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.expense import (
    Expense,
    ExpenseType,
    PaymentMethod,
    ReceiptStatus,
    ReviewStatus,
)
from app.models.job import Job, JobCategoryBudget, JobStatus
from app.services.budget_summary import (
    DEFAULT_WARNING_AMBER_PCT,
    DEFAULT_WARNING_RED_PCT,
    _effective_thresholds,
    compute_band,
    summarize_job,
    summarize_jobs,
)
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


# ===========================================================================
# Phase 3 Lite+ — effective thresholds (point 3 of the 2026-05-10 review)
# ===========================================================================


def test_effective_thresholds_pure_passthrough():
    """Per-job overrides come back unchanged — no defaults applied."""
    amber, red = _effective_thresholds(Decimal("70.00"), Decimal("90.00"))
    assert amber == Decimal("70.00")
    assert red == Decimal("90.00")


def test_effective_thresholds_pure_fallback_both_null():
    """NULL stored values fall back to system defaults (80 / 100)."""
    amber, red = _effective_thresholds(None, None)
    assert amber == DEFAULT_WARNING_AMBER_PCT
    assert amber == Decimal("80.00")
    assert red == DEFAULT_WARNING_RED_PCT
    assert red == Decimal("100.00")


def test_effective_thresholds_pure_fallback_mixed():
    """One stored, one NULL — only the NULL one falls back."""
    amber_only, red_default = _effective_thresholds(Decimal("70.00"), None)
    assert amber_only == Decimal("70.00")
    assert red_default == DEFAULT_WARNING_RED_PCT

    amber_default, red_only = _effective_thresholds(None, Decimal("90.00"))
    assert amber_default == DEFAULT_WARNING_AMBER_PCT
    assert red_only == Decimal("90.00")


@pytest.mark.asyncio
async def test_summarize_jobs_carries_effective_thresholds_with_defaults(
    db_session, seeded_admin
):
    """JobSummary always carries effective_warning_*_pct, NEVER NULL."""
    job = await _mk_job(db_session, seeded_admin)  # both stored thresholds NULL
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.effective_warning_amber_pct == Decimal("80.00")
    assert s.effective_warning_red_pct == Decimal("100.00")


@pytest.mark.asyncio
async def test_summarize_jobs_carries_effective_thresholds_with_overrides(
    db_session, seeded_admin
):
    """When stored values are set, the effective fields carry them through."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        warning_amber_pct=Decimal("70.00"),
        warning_red_pct=Decimal("90.00"),
    )
    s = (await summarize_jobs(db_session, job_ids=[job.job_id]))[job.job_id]
    assert s.effective_warning_amber_pct == Decimal("70.00")
    assert s.effective_warning_red_pct == Decimal("90.00")


@pytest.mark.asyncio
async def test_summarize_jobs_does_not_overwrite_stored_thresholds(
    db_session, seeded_admin
):
    """Computing the summary must NOT mutate the stored NULL columns.

    Point 3 of the operator review: stored values stay NULL when not
    overridden; the API surfaces the fallback only via the separate
    ``effective_*`` fields, never by writing the default back to the
    column.
    """
    job = await _mk_job(db_session, seeded_admin)  # NULL thresholds
    _ = await summarize_jobs(db_session, job_ids=[job.job_id])
    # Re-read from DB to confirm the columns are still NULL.
    await db_session.refresh(job)
    assert job.warning_amber_pct is None
    assert job.warning_red_pct is None


# ===========================================================================
# Phase 3 Lite+ — chip band logic (point 2 of the 2026-05-10 review)
# ===========================================================================


def _band(percent, remaining=None, total_budget=Decimal("1000"),
          amber=Decimal("80"), red=Decimal("100")):
    """Tiny shortcut so the band-routing tests stay one line each."""
    p = None if percent is None else Decimal(str(percent))
    r = None if remaining is None else Decimal(str(remaining))
    tb = None if total_budget is None else Decimal(str(total_budget))
    return compute_band(p, r, tb, amber, red)


def test_band_no_budget_when_total_budget_null():
    assert _band(percent=None, total_budget=None) == "no_budget"


def test_band_no_budget_when_total_budget_zero():
    assert _band(percent=None, total_budget=0) == "no_budget"


def test_band_on_track_below_amber():
    assert _band(percent="50") == "on_track"
    assert _band(percent="79.99") == "on_track"


def test_band_approaching_at_or_above_amber_below_red():
    assert _band(percent="80") == "approaching"
    assert _band(percent="99.99") == "approaching"


def test_band_critical_only_when_red_below_100():
    """Custom red threshold below 100 enables the ``critical`` band."""
    # red = 90, percent = 92 → critical (red ≤ % < 100)
    assert _band(percent="92", red=Decimal("90")) == "critical"
    assert _band(percent="90", red=Decimal("90")) == "critical"
    # Just below the custom red is still approaching
    assert _band(percent="89.99", red=Decimal("90")) == "approaching"


def test_band_critical_collapses_when_red_is_100():
    """With default red = 100, critical band is empty.

    A consumption of 99.99 stays ``approaching``; 100 becomes
    ``over_budget``. There is no value at which ``critical`` fires
    when red = 100.
    """
    assert _band(percent="99.99", red=Decimal("100")) == "approaching"
    assert _band(percent="100", red=Decimal("100")) == "over_budget"


def test_band_over_budget_when_percent_at_100_or_above():
    assert _band(percent="100") == "over_budget"
    assert _band(percent="100.01") == "over_budget"
    assert _band(percent="500") == "over_budget"


def test_band_over_budget_when_remaining_negative():
    """Even if percent < 100, negative remaining flips to over_budget."""
    # Edge case from the rule: percent might round under 100 due to
    # quantization, but remaining_ex_gst is the source of truth for
    # whether the budget is actually exhausted.
    assert _band(percent="99.99", remaining="-0.01") == "over_budget"


def test_band_over_budget_wins_over_critical():
    """Tie-break: when both rules would match, over_budget wins."""
    # Custom red = 90, percent = 100 → both critical-range AND over → over_budget
    assert _band(percent="100", red=Decimal("90")) == "over_budget"


def test_band_custom_red_below_100_does_not_label_over_budget():
    """Frozen rule: ``Over budget`` MUST NOT appear at 83% with red=90.

    This is the misleading scenario from the v1 plan that point 2 of
    the operator review explicitly corrected.
    """
    # Live 晶晶 numbers (83.61% consumed, $30,820 remaining): with custom
    # red = 90, this should be ``critical`` (orange), NOT ``over_budget``.
    band = _band(
        percent="83.61",
        remaining="30820.00",
        total_budget="188000.00",
        amber=Decimal("60"),
        red=Decimal("90"),
    )
    assert band == "approaching"  # 83.61 < red 90 → still approaching
    # And bumping past the red:
    band2 = _band(
        percent="91.00",
        remaining="16920.00",
        total_budget="188000.00",
        amber=Decimal("60"),
        red=Decimal("90"),
    )
    assert band2 == "critical"
    # And actually exceeding the budget (lower budget so % > 100):
    band3 = _band(
        percent="157.18",
        remaining="-57180.00",
        total_budget="100000.00",
        amber=Decimal("60"),
        red=Decimal("90"),
    )
    assert band3 == "over_budget"


# ===========================================================================
# Phase 3 Lite+ — derived margin fields
# ===========================================================================


@pytest.mark.asyncio
async def test_margin_target_passthrough(db_session, seeded_admin):
    job = await _mk_job(
        db_session, seeded_admin, target_profit_ratio_pct=Decimal("15.00")
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.target_profit_ratio_pct == Decimal("15.00")


@pytest.mark.asyncio
async def test_margin_target_cost_limit_math(db_session, seeded_admin):
    """contract * (1 - target/100) → target_cost_limit_ex_gst."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.target_cost_limit_ex_gst == Decimal("170000.00")


@pytest.mark.asyncio
async def test_margin_target_cost_limit_null_when_contract_missing(
    db_session, seeded_admin
):
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=None,
        target_profit_ratio_pct=Decimal("15.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.target_cost_limit_ex_gst is None


@pytest.mark.asyncio
async def test_margin_target_cost_limit_null_when_target_missing(
    db_session, seeded_admin
):
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=None,
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.target_cost_limit_ex_gst is None


@pytest.mark.asyncio
async def test_margin_budgeted_profit_math(db_session, seeded_admin):
    """contract - total_budget → budgeted_profit_ex_gst."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        total_budget_ex_gst=Decimal("188000.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.budgeted_profit_ex_gst == Decimal("12000.00")


@pytest.mark.asyncio
async def test_margin_budgeted_profit_negative_allowed(db_session, seeded_admin):
    """Budget greater than contract → negative budgeted_profit (allowed)."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("100000.00"),
        total_budget_ex_gst=Decimal("120000.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.budgeted_profit_ex_gst == Decimal("-20000.00")
    assert s.budgeted_profit_ratio_pct == Decimal("-20.00")


@pytest.mark.asyncio
async def test_margin_budgeted_profit_null_when_either_input_missing(
    db_session, seeded_admin
):
    job_no_budget = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        total_budget_ex_gst=None,
    )
    s1 = await summarize_job(db_session, job_no_budget.job_id)
    assert s1.budgeted_profit_ex_gst is None
    assert s1.budgeted_profit_ratio_pct is None

    job_no_contract = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=None,
        total_budget_ex_gst=Decimal("188000.00"),
    )
    s2 = await summarize_job(db_session, job_no_contract.job_id)
    assert s2.budgeted_profit_ex_gst is None
    assert s2.budgeted_profit_ratio_pct is None


@pytest.mark.asyncio
async def test_margin_budgeted_profit_ratio_math(db_session, seeded_admin):
    """budgeted_profit / contract * 100 → budgeted_profit_ratio_pct."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        total_budget_ex_gst=Decimal("188000.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    assert s.budgeted_profit_ratio_pct == Decimal("6.00")


@pytest.mark.asyncio
async def test_margin_budgeted_profit_ratio_null_when_contract_zero(
    db_session, seeded_admin
):
    """No divide-by-zero when contract is 0; ratio comes back NULL."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("0"),
        total_budget_ex_gst=Decimal("100.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    # budgeted_profit = 0 - 100 = -100 (still computable)
    assert s.budgeted_profit_ex_gst == Decimal("-100.00")
    # ratio undefined
    assert s.budgeted_profit_ratio_pct is None


@pytest.mark.asyncio
async def test_margin_budget_delta_vs_target_cost_positive(
    db_session, seeded_admin
):
    """Budget exceeds target cost limit → positive delta (lower margin)."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
        total_budget_ex_gst=Decimal("188000.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    # target_cost_limit = 170000.00 ; delta = 188000 - 170000 = 18000.00
    assert s.budget_delta_vs_target_cost_ex_gst == Decimal("18000.00")


@pytest.mark.asyncio
async def test_margin_budget_delta_vs_target_cost_negative(
    db_session, seeded_admin
):
    """Budget below target cost limit → negative delta (more conservative)."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
        total_budget_ex_gst=Decimal("160000.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    # target_cost_limit = 170000.00 ; delta = 160000 - 170000 = -10000.00
    assert s.budget_delta_vs_target_cost_ex_gst == Decimal("-10000.00")


@pytest.mark.asyncio
async def test_margin_budget_delta_null_when_any_input_missing(
    db_session, seeded_admin
):
    """All three of contract, target, budget required for the delta."""
    # Missing target
    job_a = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=None,
        total_budget_ex_gst=Decimal("188000.00"),
    )
    assert (
        await summarize_job(db_session, job_a.job_id)
    ).budget_delta_vs_target_cost_ex_gst is None

    # Missing budget
    job_b = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
        total_budget_ex_gst=None,
    )
    assert (
        await summarize_job(db_session, job_b.job_id)
    ).budget_delta_vs_target_cost_ex_gst is None

    # Missing contract
    job_c = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=None,
        target_profit_ratio_pct=Decimal("15.00"),
        total_budget_ex_gst=Decimal("188000.00"),
    )
    assert (
        await summarize_job(db_session, job_c.job_id)
    ).budget_delta_vs_target_cost_ex_gst is None


@pytest.mark.asyncio
async def test_summarize_job_carries_effective_thresholds(
    db_session, seeded_admin
):
    """JobBudgetSummary mirrors JobSummary's effective threshold contract."""
    job_default = await _mk_job(db_session, seeded_admin)
    s1 = await summarize_job(db_session, job_default.job_id)
    assert s1.effective_warning_amber_pct == Decimal("80.00")
    assert s1.effective_warning_red_pct == Decimal("100.00")

    job_override = await _mk_job(
        db_session,
        seeded_admin,
        warning_amber_pct=Decimal("70.00"),
        warning_red_pct=Decimal("90.00"),
    )
    s2 = await summarize_job(db_session, job_override.job_id)
    assert s2.effective_warning_amber_pct == Decimal("70.00")
    assert s2.effective_warning_red_pct == Decimal("90.00")


@pytest.mark.asyncio
async def test_summarize_job_no_actual_profit_field_present(
    db_session, seeded_admin
):
    """Phase 3 Lite+ explicitly removed actual_profit_*; verify absence.

    Point 1 of the 2026-05-10 operator review: mid-project actual
    profit is misleading because future costs are unknown. The schema
    must not carry an ``actual_profit_*`` field; the UI may surface
    ``contract − cost_to_date`` only as a low-emphasis "remaining
    contract value" line with a "not actual profit" disclaimer.
    """
    job = await _mk_job(
        db_session,
        seeded_admin,
        contract_value_ex_gst=Decimal("200000.00"),
        total_budget_ex_gst=Decimal("188000.00"),
        target_profit_ratio_pct=Decimal("15.00"),
    )
    s = await summarize_job(db_session, job.job_id)
    # The schema model itself must not declare these field names.
    assert not hasattr(s, "actual_profit_ex_gst")
    assert not hasattr(s, "actual_profit_ratio_pct")


# ===========================================================================
# Phase 3 Lite+ — DB CHECK constraints (backstop behind Pydantic)
#
# These tests bypass Pydantic by constructing Job() ORM objects directly
# with violating values. The DB CHECK constraints must fire on flush and
# raise IntegrityError. Each test runs in a SAVEPOINT so the violation
# does not poison the test fixture's outer transaction.
# ===========================================================================


async def _try_violation(db, **bad_kwargs):
    """Attempt the violating insert inside a SAVEPOINT and return the error."""
    raised: IntegrityError | None = None
    sp = await db.begin_nested()
    try:
        bad_kwargs.setdefault("job_id", uuid.uuid4())
        bad_kwargs.setdefault("job_name", "violator")
        bad_kwargs.setdefault("status", JobStatus.active)
        # created_by must satisfy the FK; pull any user from the session.
        if "created_by" not in bad_kwargs:
            from sqlalchemy import select as _sel

            from app.models.user import User
            u = (await db.execute(_sel(User).limit(1))).scalar_one()
            bad_kwargs["created_by"] = u.user_id
        db.add(Job(**bad_kwargs))
        await db.flush()
    except IntegrityError as exc:
        raised = exc
        await sp.rollback()
    else:
        await sp.rollback()
    return raised


@pytest.mark.asyncio
async def test_check_target_profit_ratio_pct_at_100_violates(
    db_session, seeded_admin
):
    """target = 100 violates the strict-less-than 100 CHECK."""
    err = await _try_violation(
        db_session, target_profit_ratio_pct=Decimal("100.00")
    )
    assert err is not None
    assert "ck_jobs_target_profit_ratio_pct_range" in str(err.orig)


@pytest.mark.asyncio
async def test_check_target_profit_ratio_pct_negative_violates(
    db_session, seeded_admin
):
    err = await _try_violation(
        db_session, target_profit_ratio_pct=Decimal("-1.00")
    )
    assert err is not None
    assert "ck_jobs_target_profit_ratio_pct_range" in str(err.orig)


@pytest.mark.asyncio
async def test_check_target_profit_ratio_pct_at_99_99_succeeds(
    db_session, seeded_admin
):
    """Boundary inclusivity — 99.99 is the largest valid value."""
    job = await _mk_job(
        db_session,
        seeded_admin,
        target_profit_ratio_pct=Decimal("99.99"),
    )
    assert job.target_profit_ratio_pct == Decimal("99.99")


@pytest.mark.asyncio
async def test_check_target_profit_ratio_pct_at_zero_succeeds(
    db_session, seeded_admin
):
    """Break-even target (0%) is allowed."""
    job = await _mk_job(
        db_session, seeded_admin, target_profit_ratio_pct=Decimal("0.00")
    )
    assert job.target_profit_ratio_pct == Decimal("0.00")


@pytest.mark.asyncio
async def test_check_warning_amber_pct_negative_violates(
    db_session, seeded_admin
):
    err = await _try_violation(
        db_session, warning_amber_pct=Decimal("-0.01")
    )
    assert err is not None
    assert "ck_jobs_warning_amber_pct_nonneg" in str(err.orig)


@pytest.mark.asyncio
async def test_check_warning_red_pct_zero_violates(db_session, seeded_admin):
    """Red must be strictly positive — 0 is not allowed."""
    err = await _try_violation(
        db_session, warning_red_pct=Decimal("0.00")
    )
    assert err is not None
    assert "ck_jobs_warning_red_pct_positive" in str(err.orig)


@pytest.mark.asyncio
async def test_check_warning_amber_lt_red_violates_when_equal(
    db_session, seeded_admin
):
    """Strict less-than: amber == red is a violation."""
    err = await _try_violation(
        db_session,
        warning_amber_pct=Decimal("80.00"),
        warning_red_pct=Decimal("80.00"),
    )
    assert err is not None
    assert "ck_jobs_warning_amber_lt_red" in str(err.orig)


@pytest.mark.asyncio
async def test_check_warning_amber_lt_red_violates_when_amber_greater(
    db_session, seeded_admin
):
    err = await _try_violation(
        db_session,
        warning_amber_pct=Decimal("90.00"),
        warning_red_pct=Decimal("80.00"),
    )
    assert err is not None
    assert "ck_jobs_warning_amber_lt_red" in str(err.orig)


@pytest.mark.asyncio
async def test_check_only_amber_set_succeeds(db_session, seeded_admin):
    """One-side-only thresholds are allowed (NULL-safe constraint)."""
    j1 = await _mk_job(
        db_session, seeded_admin, warning_amber_pct=Decimal("80.00")
    )
    assert j1.warning_amber_pct == Decimal("80.00")
    assert j1.warning_red_pct is None


@pytest.mark.asyncio
async def test_check_only_red_set_succeeds(db_session, seeded_admin):
    j2 = await _mk_job(
        db_session, seeded_admin, warning_red_pct=Decimal("100.00")
    )
    assert j2.warning_amber_pct is None
    assert j2.warning_red_pct == Decimal("100.00")


@pytest.mark.asyncio
async def test_check_constraints_present_on_table(db_session):
    """Sanity: the four named CHECK constraints exist in the live schema.

    Catches the case where the migration ran but ``Base.metadata.
    create_all`` (used by the test bootstrap) didn't pick up the
    constraints because they weren't mirrored in the SQLAlchemy model's
    ``__table_args__``.
    """
    rows = (
        await db_session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'jobs'::regclass AND contype = 'c'"
            )
        )
    ).all()
    names = {r[0] for r in rows}
    expected = {
        "ck_jobs_target_profit_ratio_pct_range",
        "ck_jobs_warning_amber_pct_nonneg",
        "ck_jobs_warning_red_pct_positive",
        "ck_jobs_warning_amber_lt_red",
    }
    missing = expected - names
    assert not missing, f"missing CHECK constraints: {missing}"


# ---------------------------------------------------------------------------
# CHP-7: uncategorised_actual_ex_gst surfaces NULL-category spend so
# the per-category list reconciles with the job-level actual_ex_gst.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp7_uncategorised_zero_when_all_categorised(
    db_session, seeded_admin, seed_categories
):
    """All expenses have categories → ``uncategorised_actual_ex_gst == 0``."""
    job = await _mk_job(db_session, seeded_admin, name="AllCategorisedJob")
    plumbing = next(c for c in seed_categories if c.category_name == "Plumbing")
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("550"),
        category_id=plumbing.category_id,
    )
    summary = await summarize_job(db_session, job.job_id)

    assert summary.uncategorised_actual_ex_gst == Decimal("0.00")
    # Reconciliation invariant: actual_ex_gst == sum(cat.actual_ex_gst) + uncategorised
    cat_sum = sum(
        (Decimal(c.actual_ex_gst) for c in summary.categories), start=Decimal("0")
    )
    assert summary.actual_ex_gst == cat_sum + summary.uncategorised_actual_ex_gst


@pytest.mark.asyncio
async def test_chp7_uncategorised_sums_null_category_expenses(
    db_session, seeded_admin, seed_categories
):
    """One categorised + one NULL-category expense → uncategorised field
    captures the NULL one exactly; categories list captures the other.

    Reconciliation invariant must hold to the cent.
    """
    job = await _mk_job(db_session, seeded_admin, name="MixedJob")
    plumbing = next(c for c in seed_categories if c.category_name == "Plumbing")
    # Categorised: $1,100 inc / $1,000 ex
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("1100"),
        category_id=plumbing.category_id,
    )
    # Uncategorised: $440 inc / $400 ex
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("440"),
        category_id=None,
    )
    summary = await summarize_job(db_session, job.job_id)

    # Uncategorised total reflects ONLY the null-category expense.
    assert summary.uncategorised_actual_ex_gst == Decimal("400.00")
    # Categories list contains exactly the Plumbing row; its actual is
    # $1,000 ex (not inflated by the uncategorised row).
    cat_rows = [c for c in summary.categories if c.category_name == "Plumbing"]
    assert len(cat_rows) == 1
    assert cat_rows[0].actual_ex_gst == Decimal("1000.00")
    # Reconciliation: $1,000 + $400 = $1,400 == actual_ex_gst.
    assert summary.actual_ex_gst == Decimal("1400.00")
    cat_sum = sum(
        (Decimal(c.actual_ex_gst) for c in summary.categories), start=Decimal("0")
    )
    assert summary.actual_ex_gst == cat_sum + summary.uncategorised_actual_ex_gst


@pytest.mark.asyncio
async def test_chp7_uncategorised_excludes_rejected(
    db_session, seeded_admin, seed_categories
):
    """Rejected NULL-category expenses must NOT contribute to the
    uncategorised total. Mirrors the existing job-level rejection-filter
    contract (``review_status IN _INCLUDED_STATUSES``).
    """
    job = await _mk_job(db_session, seeded_admin, name="RejectedNullJob")
    # Reviewed, uncategorised → counts.
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("220"),
        category_id=None,
    )
    # Rejected, uncategorised → MUST NOT count.
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("99999"),
        category_id=None,
        review_status=ReviewStatus.rejected,
    )
    summary = await summarize_job(db_session, job.job_id)

    # Only the $220 inc / $200 ex reviewed entry contributes.
    assert summary.uncategorised_actual_ex_gst == Decimal("200.00")
    assert summary.actual_ex_gst == Decimal("200.00")


@pytest.mark.asyncio
async def test_chp7_uncategorised_includes_pending(
    db_session, seeded_admin, seed_categories
):
    """Pending NULL-category expenses contribute (matches dashboard rule)."""
    job = await _mk_job(db_session, seeded_admin, name="PendingNullJob")
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("330"),
        category_id=None,
        review_status=ReviewStatus.pending,
    )
    summary = await summarize_job(db_session, job.job_id)

    assert summary.uncategorised_actual_ex_gst == Decimal("300.00")


@pytest.mark.asyncio
async def test_chp7_categories_list_unchanged_when_no_uncategorised(
    db_session, seeded_admin, seed_categories
):
    """Existing per-category list semantics are NOT regressed by CHP-7.
    The list rule (omit zero-zero rows; include any row with a budget OR
    a non-rejected expense) keeps working unchanged.
    """
    job = await _mk_job(db_session, seeded_admin, name="UnchangedCatsJob")
    plumbing = next(c for c in seed_categories if c.category_name == "Plumbing")
    concrete = next(c for c in seed_categories if c.category_name == "Concrete")

    # Categorised expense + a budget for a DIFFERENT category with no
    # actual spend. The list should contain both rows; uncategorised = 0.
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("550"),
        category_id=plumbing.category_id,
    )
    await _mk_budget(
        db_session,
        job=job,
        category_id=concrete.category_id,
        budget_amount_ex_gst=Decimal("10000"),
    )
    summary = await summarize_job(db_session, job.job_id)

    cat_names = {c.category_name for c in summary.categories}
    assert cat_names == {"Plumbing", "Concrete"}
    assert summary.uncategorised_actual_ex_gst == Decimal("0.00")


@pytest.mark.asyncio
async def test_chp7_uncategorised_only_no_category_rows(
    db_session, seeded_admin, seed_categories
):
    """When ALL of the job's spend has ``category_id IS NULL``, the
    categories list is empty but uncategorised_actual_ex_gst is
    populated. Reconciliation still holds.
    """
    job = await _mk_job(db_session, seeded_admin, name="AllUncategorisedJob")
    await _mk_expense(
        db_session,
        job=job,
        admin=seeded_admin,
        amount_inc_gst=Decimal("770"),
        category_id=None,
    )
    summary = await summarize_job(db_session, job.job_id)

    assert summary.categories == []
    assert summary.uncategorised_actual_ex_gst == Decimal("700.00")
    assert summary.actual_ex_gst == Decimal("700.00")
