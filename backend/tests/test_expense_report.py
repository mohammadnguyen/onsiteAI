"""GET /reports/expenses-report — the PDF report's data endpoint.

Founder decision 2026-08-24. The endpoint must agree with the Excel
export about WHICH expenses exist (shared frozen inclusion rule) and
must compute every aggregate server-side.
"""

import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Category,
    Expense,
    ExpenseType,
    Job,
    JobStatus,
    PaymentMethod,
    ReceiptStatus,
    ReviewStatus,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _today() -> _datetime.date:
    return _datetime.datetime.now(_datetime.UTC).date()


async def _mk_job(
    db_session,
    admin,
    *,
    name: str,
    budget: str | None = None,
    contract: str | None = None,
    code: str | None = None,
) -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        job_code=code,
        status=JobStatus.active,
        total_budget_ex_gst=Decimal(budget) if budget else None,
        contract_value_ex_gst=Decimal(contract) if contract else None,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _mk_expense(
    db_session,
    admin,
    *,
    job,
    inc: str,
    gst: str,
    ex: str,
    when=None,
    category=None,
    review=ReviewStatus.reviewed,
    receipt=ReceiptStatus.no_receipt,
    description: str = "x",
) -> Expense:
    e = Expense(
        expense_id=uuid.uuid4(),
        job_id=job.job_id,
        expense_type=ExpenseType.supplier_expense,
        description=description,
        amount_inc_gst=Decimal(inc),
        gst_amount=Decimal(gst),
        amount_ex_gst=Decimal(ex),
        payment_method=PaymentMethod.transfer,
        receipt_status=receipt,
        review_status=review,
        expense_date=when or _today(),
        category_id=category.category_id if category else None,
        entered_by_user_id=admin.user_id,
    )
    db_session.add(e)
    await db_session.flush()
    return e


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_requires_admin(client, contributor_token):
    r = await client.get(
        "/reports/expenses-report", headers=_auth(contributor_token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_report_empty_is_valid(client, admin_token):
    r = await client.get("/reports/expenses-report", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["jobs"] == [] and b["expenses"] == []
    assert Decimal(b["totals"]["actual_inc_gst"]) == Decimal("0.00")
    assert b["meta"]["job_count"] == 0


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_totals_and_job_rollup(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(
        db_session, seeded_admin, name="Site A", budget="1000", contract="2000"
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100"
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="220", gst="20", ex="200"
    )

    r = await client.get("/reports/expenses-report", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    b = r.json()
    assert Decimal(b["totals"]["actual_inc_gst"]) == Decimal("330")
    assert Decimal(b["totals"]["gst_amount"]) == Decimal("30")
    assert Decimal(b["totals"]["actual_ex_gst"]) == Decimal("300")
    assert b["meta"]["expense_count"] == 2

    row = b["jobs"][0]
    assert row["job_name"] == "Site A"
    assert row["expense_count"] == 2
    assert Decimal(row["period_inc_gst"]) == Decimal("330")
    assert Decimal(row["total_budget_ex_gst"]) == Decimal("1000")
    assert Decimal(row["contract_value_ex_gst"]) == Decimal("2000")
    # Budget position is ALL-TIME, not period-scoped.
    assert Decimal(row["all_time_ex_gst"]) == Decimal("300")
    assert Decimal(row["remaining_ex_gst"]) == Decimal("700")
    assert row["overspend"] is False


@pytest.mark.asyncio
async def test_report_jobs_sorted_by_spend_desc(
    client, db_session, seeded_admin, admin_token
):
    small = await _mk_job(db_session, seeded_admin, name="Small")
    big = await _mk_job(db_session, seeded_admin, name="Big")
    await _mk_expense(
        db_session, seeded_admin, job=small, inc="110", gst="10", ex="100"
    )
    await _mk_expense(
        db_session, seeded_admin, job=big, inc="1100", gst="100", ex="1000"
    )

    r = await client.get("/reports/expenses-report", headers=_auth(admin_token))
    assert [j["job_name"] for j in r.json()["jobs"]] == ["Big", "Small"]


@pytest.mark.asyncio
async def test_report_categories_and_uncategorised(
    client, db_session, seeded_admin, admin_token, seed_categories
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    cat = (await db_session.execute(select(Category))).scalars().first()
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100",
        category=cat,
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="220", gst="20", ex="200"
    )

    r = await client.get("/reports/expenses-report", headers=_auth(admin_token))
    cats = r.json()["categories"]
    # Uncategorised is its own row with a null id — never folded away.
    assert any(c["category_id"] is None for c in cats)
    # Sorted by spend: the 200 uncategorised row leads.
    assert Decimal(cats[0]["actual_ex_gst"]) == Decimal("200")
    assert cats[0]["category_id"] is None


@pytest.mark.asyncio
async def test_report_months_series(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100",
        when=_datetime.date(2026, 3, 5),
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="220", gst="20", ex="200",
        when=_datetime.date(2026, 4, 2),
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="330", gst="30", ex="300",
        when=_datetime.date(2026, 4, 20),
    )

    r = await client.get(
        "/reports/expenses-report?from_date=2026-01-01&to_date=2026-12-31",
        headers=_auth(admin_token),
    )
    months = r.json()["months"]
    assert [m["month"] for m in months] == ["2026-03", "2026-04"]
    assert Decimal(months[1]["actual_inc_gst"]) == Decimal("550")


# ---------------------------------------------------------------------------
# Inclusion rule parity with the Excel export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_excludes_pending_by_default_and_rejected_always(
    client, db_session, seeded_admin, admin_token
):
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100"
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="999", gst="0", ex="999",
        review=ReviewStatus.pending,
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="888", gst="0", ex="888",
        review=ReviewStatus.rejected,
    )

    r = await client.get("/reports/expenses-report", headers=_auth(admin_token))
    assert Decimal(r.json()["totals"]["actual_inc_gst"]) == Decimal("110")

    r = await client.get(
        "/reports/expenses-report?include_pending=true",
        headers=_auth(admin_token),
    )
    b = r.json()
    # Pending now counted; rejected still excluded.
    assert Decimal(b["totals"]["actual_inc_gst"]) == Decimal("1109")
    assert b["meta"]["include_pending"] is True


# ---------------------------------------------------------------------------
# Filters + validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_date_and_job_filters(
    client, db_session, seeded_admin, admin_token
):
    a = await _mk_job(db_session, seeded_admin, name="A")
    b_job = await _mk_job(db_session, seeded_admin, name="B")
    await _mk_expense(
        db_session, seeded_admin, job=a, inc="110", gst="10", ex="100",
        when=_datetime.date(2026, 3, 5),
    )
    await _mk_expense(
        db_session, seeded_admin, job=b_job, inc="220", gst="20", ex="200",
        when=_datetime.date(2026, 5, 5),
    )

    r = await client.get(
        f"/reports/expenses-report?job_id={a.job_id}", headers=_auth(admin_token)
    )
    assert r.json()["meta"]["job_count"] == 1

    r = await client.get(
        "/reports/expenses-report?from_date=2026-04-01",
        headers=_auth(admin_token),
    )
    assert [j["job_name"] for j in r.json()["jobs"]] == ["B"]


@pytest.mark.asyncio
async def test_report_rejects_inverted_range_and_unknown_job(
    client, admin_token
):
    r = await client.get(
        "/reports/expenses-report?from_date=2026-05-01&to_date=2026-04-01",
        headers=_auth(admin_token),
    )
    assert r.status_code == 400
    r = await client.get(
        f"/reports/expenses-report?job_id={uuid.uuid4()}",
        headers=_auth(admin_token),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Receipts: counted, never editorialised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_receipt_counts(
    client, db_session, seeded_admin, admin_token
):
    """No "attached" state exists yet (Phase 5), so receipts_on_file is
    structurally 0; expected_later is the only signal available."""
    job = await _mk_job(db_session, seeded_admin, name="Site A")
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100"
    )
    await _mk_expense(
        db_session, seeded_admin, job=job, inc="110", gst="10", ex="100",
        receipt=ReceiptStatus.expected_later,
    )

    t = (
        await client.get(
            "/reports/expenses-report", headers=_auth(admin_token)
        )
    ).json()["totals"]
    assert t["receipts_on_file"] == 0
    assert t["receipts_expected_later"] == 1
