"""Phase 2 Task T-J: DB-backed tests for the duplicate detector.

Exercises :func:`app.services.parser.duplicates.detect_duplicate`
against real Postgres (5433). Every test seeds a small expense graph
— two jobs + two suppliers + a reference "original" expense — then
calls the detector with drafted tuples and asserts on the narrow
:class:`DuplicateMatch` result.

Tests cover:

* the 4-condition rule: job, amount, ±1 day, supplier-or-description
* the description branch firing when either side lacks a supplier
* rejected expenses excluded from candidates (soft-deleted)
* earliest-match semantics when multiple priors match
* purity — the detector does not write to the DB
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Expense,
    ExpenseType,
    Job,
    JobStatus,
    Supplier,
)
from app.models.expense import ReviewStatus
from app.services.parser.duplicates import DuplicateMatch, detect_duplicate


async def _make_job(
    db_session,
    admin,
    *,
    name: str,
    code: str,
) -> Job:
    """Insert a :class:`Job` into the current transaction."""
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


async def _make_supplier(db_session, *, name: str) -> Supplier:
    """Insert an active :class:`Supplier` into the current transaction."""
    supplier = Supplier(
        supplier_id=uuid.uuid4(),
        supplier_name=name,
        is_active=True,
    )
    db_session.add(supplier)
    await db_session.flush()
    return supplier


async def _make_expense(
    db_session,
    admin,
    *,
    job: Job,
    amount: Decimal,
    on_date: date,
    supplier: Supplier | None,
    description: str | None,
    review_status: ReviewStatus = ReviewStatus.reviewed,
    expense_type: ExpenseType = ExpenseType.supplier_expense,
) -> Expense:
    """Insert an :class:`Expense` row. Returns the persisted expense."""
    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        supplier_id=supplier.supplier_id if supplier is not None else None,
        entered_by_user_id=admin.user_id,
        expense_type=expense_type,
        description=description,
        amount_inc_gst=amount,
        expense_date=on_date,
        review_status=review_status,
    )
    db_session.add(expense)
    await db_session.flush()
    return expense


@pytest_asyncio.fixture
async def seeded_duplicate_setup(db_session, seeded_admin):
    """Seed two jobs, two suppliers, and one reference "original" expense.

    * Job A — ``Kelly House`` (code ``KH-01``)
    * Job B — ``Smith Reno`` (code ``SR-02``)
    * Supplier 1 — ``Bunnings``
    * Supplier 2 — ``Mitre 10``
    * Original expense — Job A, $305.00, 2026-04-15, Supplier 1,
      description "timber framing", review_status=reviewed.

    Returned as a dict for named access from the test body.
    """
    job_a = await _make_job(db_session, seeded_admin, name="Kelly House", code="KH-01")
    job_b = await _make_job(db_session, seeded_admin, name="Smith Reno", code="SR-02")
    sup_1 = await _make_supplier(db_session, name="Bunnings")
    sup_2 = await _make_supplier(db_session, name="Mitre 10")

    original_date = date(2026, 4, 15)
    original = await _make_expense(
        db_session,
        seeded_admin,
        job=job_a,
        amount=Decimal("305.00"),
        on_date=original_date,
        supplier=sup_1,
        description="timber framing",
    )

    return {
        "admin": seeded_admin,
        "job_a": job_a,
        "job_b": job_b,
        "sup_1": sup_1,
        "sup_2": sup_2,
        "original": original,
        "original_date": original_date,
    }


# ---------------------------------------------------------------------------
# Core matching rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_match_same_day_same_supplier(db_session, seeded_duplicate_setup):
    """Draft identical to the original → found; returns original's id."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"],
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result == DuplicateMatch(duplicate_of_expense_id=s["original"].expense_id, found=True)


@pytest.mark.asyncio
async def test_match_plus_one_day(db_session, seeded_duplicate_setup):
    """Draft dated the day AFTER the original → still found (±1 day)."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"] + timedelta(days=1),
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == s["original"].expense_id


@pytest.mark.asyncio
async def test_match_minus_one_day(db_session, seeded_duplicate_setup):
    """Draft dated the day BEFORE the original → still found (±1 day)."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"] - timedelta(days=1),
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == s["original"].expense_id


@pytest.mark.asyncio
async def test_no_match_plus_two_days(db_session, seeded_duplicate_setup):
    """Draft dated two days after the original → outside the window."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"] + timedelta(days=2),
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result == DuplicateMatch(duplicate_of_expense_id=None, found=False)


@pytest.mark.asyncio
async def test_no_match_different_job(db_session, seeded_duplicate_setup):
    """Same tuple but Job B → not found (job_id gate)."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_b"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"],
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result.found is False
    assert result.duplicate_of_expense_id is None


@pytest.mark.asyncio
async def test_no_match_different_amount(db_session, seeded_duplicate_setup):
    """Same tuple but amount off by $1 → not found (amount gate)."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("306.00"),
        expense_date=s["original_date"],
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    assert result.found is False


@pytest.mark.asyncio
async def test_no_match_different_supplier(db_session, seeded_duplicate_setup):
    """Both sides have a supplier but they differ → not found."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"],
        supplier_id=s["sup_2"].supplier_id,
        description="timber framing",
    )

    assert result.found is False


# ---------------------------------------------------------------------------
# Description branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_description_match_when_supplier_absent_both(db_session, seeded_admin):
    """Both sides supplier=None, descriptions normalise equal → found."""
    job = await _make_job(db_session, seeded_admin, name="Labour Job", code="LB-01")
    prior = await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("200.00"),
        on_date=date(2026, 4, 10),
        supplier=None,
        description="Timber Framing",
        expense_type=ExpenseType.labour,
    )

    result = await detect_duplicate(
        db=db_session,
        job_id=job.job_id,
        amount_inc_gst=Decimal("200.00"),
        expense_date=date(2026, 4, 10),
        supplier_id=None,
        # Different casing + extra whitespace — normalize_alias
        # (casefold + punctuation/whitespace strip) collapses both to
        # ``timberframing``.
        description="timber   framing",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == prior.expense_id


@pytest.mark.asyncio
async def test_description_match_when_draft_has_no_supplier(db_session, seeded_duplicate_setup):
    """Draft supplier=None, prior supplier present, descriptions equal → found."""
    s = seeded_duplicate_setup
    result = await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"],
        supplier_id=None,
        description="Timber Framing",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == s["original"].expense_id


@pytest.mark.asyncio
async def test_description_match_when_prior_has_no_supplier(db_session, seeded_admin):
    """Prior supplier=None, draft supplier present, descriptions equal → found."""
    job = await _make_job(db_session, seeded_admin, name="Mixed Job", code="MX-01")
    sup = await _make_supplier(db_session, name="Drafty Supplier")
    prior = await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("99.00"),
        on_date=date(2026, 4, 12),
        supplier=None,
        description="timber framing",
        expense_type=ExpenseType.labour,
    )

    result = await detect_duplicate(
        db=db_session,
        job_id=job.job_id,
        amount_inc_gst=Decimal("99.00"),
        expense_date=date(2026, 4, 12),
        supplier_id=sup.supplier_id,
        description="Timber-Framing",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == prior.expense_id


@pytest.mark.asyncio
async def test_description_mismatch(db_session, seeded_admin):
    """Both supplier=None, descriptions differ → not found."""
    job = await _make_job(db_session, seeded_admin, name="Labour 2", code="LB-02")
    await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("150.00"),
        on_date=date(2026, 4, 10),
        supplier=None,
        description="framing",
        expense_type=ExpenseType.labour,
    )

    result = await detect_duplicate(
        db=db_session,
        job_id=job.job_id,
        amount_inc_gst=Decimal("150.00"),
        expense_date=date(2026, 4, 10),
        supplier_id=None,
        description="tiling",
    )

    assert result.found is False


# ---------------------------------------------------------------------------
# Exclusions + ordering + purity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_expenses_not_matched(db_session, seeded_admin):
    """An original with ``review_status=rejected`` is soft-deleted and must never match."""
    job = await _make_job(db_session, seeded_admin, name="RJ Job", code="RJ-01")
    sup = await _make_supplier(db_session, name="RJ Supplier")
    await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("77.00"),
        on_date=date(2026, 4, 1),
        supplier=sup,
        description="rejected row",
        review_status=ReviewStatus.rejected,
    )

    result = await detect_duplicate(
        db=db_session,
        job_id=job.job_id,
        amount_inc_gst=Decimal("77.00"),
        expense_date=date(2026, 4, 1),
        supplier_id=sup.supplier_id,
        description="rejected row",
    )

    assert result.found is False


@pytest.mark.asyncio
async def test_returns_earliest_match(db_session, seeded_admin):
    """When multiple priors match, the earliest (``created_at`` ASC) wins."""
    job = await _make_job(db_session, seeded_admin, name="Multi Job", code="ML-01")
    sup = await _make_supplier(db_session, name="Multi Supplier")

    # Two priors with the same (job, amount, date, supplier). Each flush
    # assigns a server-default ``created_at`` in insertion order, so the
    # FIRST one flushed should be the earliest by ``created_at`` ASC.
    earliest = await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("250.00"),
        on_date=date(2026, 4, 5),
        supplier=sup,
        description="first entry",
    )
    await _make_expense(
        db_session,
        seeded_admin,
        job=job,
        amount=Decimal("250.00"),
        on_date=date(2026, 4, 5),
        supplier=sup,
        description="second entry",
    )

    result = await detect_duplicate(
        db=db_session,
        job_id=job.job_id,
        amount_inc_gst=Decimal("250.00"),
        expense_date=date(2026, 4, 5),
        supplier_id=sup.supplier_id,
        description="first entry",
    )

    assert result.found is True
    assert result.duplicate_of_expense_id == earliest.expense_id


@pytest.mark.asyncio
async def test_pure_function_contract(db_session, seeded_duplicate_setup):
    """``detect_duplicate`` must not write to the DB — session stays clean."""
    s = seeded_duplicate_setup

    # Snapshot the expenses table before the call.
    before = (await db_session.execute(select(Expense))).scalars().all()
    before_ids = {e.expense_id for e in before}
    before_count = len(before)

    await detect_duplicate(
        db=db_session,
        job_id=s["job_a"].job_id,
        amount_inc_gst=Decimal("305.00"),
        expense_date=s["original_date"],
        supplier_id=s["sup_1"].supplier_id,
        description="timber framing",
    )

    # The session should have no pending changes (no new / dirty /
    # deleted objects). The fixture's transaction would catch
    # persisted writes on rollback, but an in-memory mutation would
    # show up in ``session.new / dirty / deleted``.
    assert list(db_session.new) == []
    assert list(db_session.dirty) == []
    assert list(db_session.deleted) == []

    # And no new expense rows either.
    after = (await db_session.execute(select(Expense))).scalars().all()
    assert len(after) == before_count
    assert {e.expense_id for e in after} == before_ids
