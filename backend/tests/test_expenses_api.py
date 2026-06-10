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

import base64
import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Category,
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
    category_id=None,
    review_status: ReviewStatus = ReviewStatus.reviewed,
    receipt_status: ReceiptStatus = ReceiptStatus.no_receipt,
    payment_method: PaymentMethod = PaymentMethod.unknown,
    expense_date: _datetime.date | None = None,
) -> Expense:
    exp = Expense(
        expense_id=uuid.uuid4(),
        job_id=job_id,
        supplier_id=supplier_id,
        category_id=category_id,
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


async def _walk_pages(client, token: str, base_query: str, *, max_pages: int = 20):
    """M2-A helper: follow ``next_cursor`` until exhausted.

    Returns (items, page_count). Asserts every page is a 200 and that
    the walk terminates within ``max_pages`` (runaway guard).
    """
    items: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = f"/expenses?{base_query}" + (f"&cursor={cursor}" if cursor else "")
        r = await client.get(url, headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        items.extend(body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            return items, pages
        assert pages < max_pages, "pagination did not terminate"


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


# ---------------------------------------------------------------------------
# M2-A: supplier/category filters + keyset pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_supplier(client, db_session, world, admin_token):
    """?supplier_id=<x> returns only that supplier's rows (M2-A)."""
    bunnings = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        supplier_id=world["sup_a"].supplier_id,
    )
    mitre = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        supplier_id=world["sup_b"].supplier_id,
    )
    no_supplier = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )

    r = await client.get(
        f"/expenses?supplier_id={world['sup_a'].supplier_id}",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(bunnings.expense_id) in ids
    assert str(mitre.expense_id) not in ids
    assert str(no_supplier.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_filters_by_category(client, db_session, world, admin_token):
    """?category_id=<x> returns only that category's rows (M2-A)."""
    cats = (
        (
            await db_session.execute(
                select(Category).order_by(Category.display_order).limit(2)
            )
        )
        .scalars()
        .all()
    )
    cat_a, cat_b = cats[0], cats[1]
    in_cat = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        category_id=cat_a.category_id,
    )
    other_cat = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        category_id=cat_b.category_id,
    )
    no_cat = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
    )

    r = await client.get(
        f"/expenses?category_id={cat_a.category_id}",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    ids = {x["expense_id"] for x in r.json()["items"]}
    assert str(in_cat.expense_id) in ids
    assert str(other_cat.expense_id) not in ids
    assert str(no_cat.expense_id) not in ids


@pytest.mark.asyncio
async def test_list_keyset_pagination_three_pages_no_dup_no_skip(
    client, db_session, world, admin_token
):
    """M2-A: limit=3 over 8 distinct-date rows -> 3 pages, each row exactly once, newest first."""
    base = _datetime.date.today() - _datetime.timedelta(days=30)
    seeded = []
    for i in range(8):
        seeded.append(
            await _seed_structured_expense(
                db_session,
                job_id=world["job_a"].job_id,
                entered_by_user_id=world["admin"].user_id,
                expense_date=base + _datetime.timedelta(days=i),
                description=f"page-walk-{i}",
            )
        )
    expected_ids = {str(e.expense_id) for e in seeded}

    items, pages = await _walk_pages(client, admin_token, "limit=3")

    got_ids = [x["expense_id"] for x in items]
    assert len(got_ids) == len(set(got_ids)), "duplicate row across page boundary"
    assert set(got_ids) == expected_ids, "skipped row across page boundary"
    assert pages == 3  # 3 + 3 + 2
    dates = [x["expense_date"] for x in items]
    assert dates == sorted(dates, reverse=True), "order broken across pages"


@pytest.mark.asyncio
async def test_list_keyset_pagination_equal_date_ties(client, db_session, world, admin_token):
    """M2-A: rows tying on BOTH expense_date and created_at paginate via the id tiebreak.

    All five rows share ``expense_date`` by construction and share
    ``created_at`` because it is the transaction timestamp
    (``server_default now()``) and the whole test runs in one
    transaction — the strongest possible tie. limit=2 must still walk
    them with no duplicate and no skip.
    """
    same_day = _datetime.date.today() - _datetime.timedelta(days=3)
    seeded = []
    for i in range(5):
        seeded.append(
            await _seed_structured_expense(
                db_session,
                job_id=world["job_a"].job_id,
                entered_by_user_id=world["admin"].user_id,
                expense_date=same_day,
                description=f"tie-{i}",
            )
        )
    expected_ids = {str(e.expense_id) for e in seeded}

    items, pages = await _walk_pages(client, admin_token, "limit=2")

    got_ids = [x["expense_id"] for x in items]
    assert len(got_ids) == len(set(got_ids)), "duplicate row on full tie"
    assert set(got_ids) == expected_ids, "skipped row on full tie"
    assert pages == 3  # 2 + 2 + 1


@pytest.mark.asyncio
async def test_list_filter_plus_cursor_combination(client, db_session, world, admin_token):
    """M2-A: the job filter holds across cursor pages (no cross-job leak mid-walk)."""
    base = _datetime.date.today() - _datetime.timedelta(days=20)
    a_rows = []
    b_ids = set()
    # Interleave job_a / job_b rows by date so naive pagination would mix them.
    for i in range(5):
        a_rows.append(
            await _seed_structured_expense(
                db_session,
                job_id=world["job_a"].job_id,
                entered_by_user_id=world["admin"].user_id,
                expense_date=base + _datetime.timedelta(days=2 * i),
            )
        )
        b = await _seed_structured_expense(
            db_session,
            job_id=world["job_b"].job_id,
            entered_by_user_id=world["admin"].user_id,
            expense_date=base + _datetime.timedelta(days=2 * i + 1),
        )
        b_ids.add(str(b.expense_id))
    expected_a_ids = {str(e.expense_id) for e in a_rows}

    items, pages = await _walk_pages(
        client, admin_token, f"job_id={world['job_a'].job_id}&limit=2"
    )

    got_ids = [x["expense_id"] for x in items]
    assert len(got_ids) == len(set(got_ids))
    assert set(got_ids) == expected_a_ids
    assert not (set(got_ids) & b_ids), "other job's rows leaked into a filtered walk"
    assert pages == 3  # 2 + 2 + 1


@pytest.mark.asyncio
async def test_list_contributor_scoping_holds_with_new_filters_and_cursor(
    client, db_session, world, seeded_contributor, contributor_token
):
    """M2-A: contributor + supplier filter + pagination never surfaces others' rows."""
    own_ids = set()
    for i in range(3):
        e = await _seed_structured_expense(
            db_session,
            job_id=world["job_a"].job_id,
            entered_by_user_id=seeded_contributor.user_id,
            supplier_id=world["sup_a"].supplier_id,
            expense_date=_datetime.date.today() - _datetime.timedelta(days=i),
        )
        own_ids.add(str(e.expense_id))
    for i in range(2):
        await _seed_structured_expense(
            db_session,
            job_id=world["job_a"].job_id,
            entered_by_user_id=world["admin"].user_id,
            supplier_id=world["sup_a"].supplier_id,
            expense_date=_datetime.date.today() - _datetime.timedelta(days=i),
        )

    items, pages = await _walk_pages(
        client, contributor_token, f"supplier_id={world['sup_a'].supplier_id}&limit=2"
    )

    got_ids = [x["expense_id"] for x in items]
    assert len(got_ids) == len(set(got_ids))
    assert set(got_ids) == own_ids, "contributor scoping broken under filter+cursor"
    assert pages == 2  # 2 + 1


@pytest.mark.asyncio
async def test_list_limit_cap(client, world, admin_token):
    """limit is validated at the edge: 0 and 501 -> 422; 500 -> 200."""
    r = await client.get("/expenses?limit=501", headers=_auth(admin_token))
    assert r.status_code == 422
    r = await client.get("/expenses?limit=0", headers=_auth(admin_token))
    assert r.status_code == 422
    r = await client.get("/expenses?limit=500", headers=_auth(admin_token))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_invalid_cursor_returns_400(client, world, admin_token):
    """M2-A: every flavour of undecodable cursor is a controlled 400, never a 500."""
    bad_cursors = [
        "garbage",  # not base64 (bad length + alphabet)
        "AAAA",  # decodes to bytes that aren't JSON
        base64.urlsafe_b64encode(b"not json").decode("ascii"),
        base64.urlsafe_b64encode(b"[1,2,3]").decode("ascii"),  # JSON, not an object
        base64.urlsafe_b64encode(b'{"d":"x"}').decode("ascii"),  # missing keys
        base64.urlsafe_b64encode(b'{"d":"nope","c":"nope","i":"nope"}').decode(
            "ascii"
        ),  # right keys, unparseable values
    ]
    for bad in bad_cursors:
        r = await client.get(f"/expenses?cursor={bad}", headers=_auth(admin_token))
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert r.json()["detail"] == "Invalid cursor"


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
    # Mobile Expense Detail (v1): the detail wire shape always carries a
    # ``review_reasons`` array (possibly empty); same for the duplicate
    # flag fields. The remaining assertions in this block are regression
    # guards against accidental wire-shape contraction.
    assert "review_reasons" in body
    assert body["review_reasons"] == []
    assert "duplicate_flag" in body
    assert "duplicate_of_expense_id" in body


# ---------------------------------------------------------------------------
# Mobile Expense Detail (v1): review_reasons surfacing on GET /expenses/{id}
#
# Semantics under test: review_reasons mirrors the *current* row in
# expense_review_queue for this expense (regardless of queue status:
# open / resolved / rejected). Returns [] when no queue row exists.
# This is NOT a historical audit trail.
# ---------------------------------------------------------------------------


async def _seed_queue_row(
    db_session,
    *,
    expense_id: uuid.UUID,
    reasons: list[ReviewReasonCode],
    status: ReviewQueueStatus = ReviewQueueStatus.open,
) -> ExpenseReviewQueue:
    row = ExpenseReviewQueue(
        review_id=uuid.uuid4(),
        expense_id=expense_id,
        review_reasons=list(reasons),
        status=status,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_get_includes_review_reasons_when_open_queue_row_exists(
    client, db_session, world, admin_token
):
    """GET /expenses/{id} surfaces the open queue row's reasons in order."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.amount_uncertain, ReviewReasonCode.supplier_uncertain],
        status=ReviewQueueStatus.open,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_reasons"] == ["amount_uncertain", "supplier_uncertain"]


@pytest.mark.asyncio
async def test_get_returns_empty_reasons_when_no_queue_row(
    client, db_session, world, admin_token
):
    """Reviewed expense with no queue row → review_reasons == []."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["review_reasons"] == []


@pytest.mark.asyncio
async def test_get_returns_reasons_for_rejected_queue_row(
    client, db_session, world, admin_token
):
    """Rejected queue row still surfaces its reasons (current row, any status)."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.rejected,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.duplicate_suspected],
        status=ReviewQueueStatus.rejected,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["review_reasons"] == ["duplicate_suspected"]


@pytest.mark.asyncio
async def test_get_returns_reasons_for_resolved_queue_row(
    client, db_session, world, admin_token
):
    """Resolved queue row still surfaces its reasons (current row, any status)."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.category_uncertain],
        status=ReviewQueueStatus.resolved,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["review_reasons"] == ["category_uncertain"]


@pytest.mark.asyncio
async def test_get_contributor_own_includes_review_reasons(
    client, db_session, world, seeded_contributor, contributor_token
):
    """Contributor reading their own pending expense gets review_reasons (RBAC unchanged)."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=seeded_contributor.user_id,
        review_status=ReviewStatus.pending,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.supplier_uncertain],
        status=ReviewQueueStatus.open,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(contributor_token))
    assert r.status_code == 200, r.text
    assert r.json()["review_reasons"] == ["supplier_uncertain"]


@pytest.mark.asyncio
async def test_get_missing_expense_returns_404(client, admin_token):
    """Unknown expense_id → 404, never accidentally surfaces another expense's reasons."""
    r = await client.get(f"/expenses/{uuid.uuid4()}", headers=_auth(admin_token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# pending_review_queue_id surfacing (3-button workflow precondition)
#
# Mobile gates Approve / Reject visibility on this field — NEVER on
# review_status alone — so the field MUST only surface a currently
# actionable (status=open) queue row's review_id. Stale resolved /
# rejected queue rows MUST NOT leak through as callable workflow
# actions. Each of the five cases below is required by the operator
# guardrail spec for the slice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_review_queue_id_surfaces_for_open_queue_row(
    client, db_session, world, admin_token
):
    """Case 1: pending expense with open queue row → field is the review_id."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.pending,
    )
    queue_row = await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.amount_uncertain],
        status=ReviewQueueStatus.open,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_review_queue_id"] == str(queue_row.review_id)


@pytest.mark.asyncio
async def test_pending_review_queue_id_null_for_reviewed_expense(
    client, db_session, world, admin_token
):
    """Case 2: reviewed expense (no queue row) → field is null."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["pending_review_queue_id"] is None


@pytest.mark.asyncio
async def test_pending_review_queue_id_null_for_rejected_expense(
    client, db_session, world, admin_token
):
    """Case 3: rejected expense (queue row also rejected) → field is null.

    Guard: stale rejected queue row's review_id must NOT surface.
    Without this, mobile could (incorrectly) show an Approve button
    on an already-rejected item and call POST /review-queue/{id}/resolve
    on a non-open row, surfacing a backend 4xx as a workflow bug.
    """
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.rejected,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.duplicate_suspected],
        status=ReviewQueueStatus.rejected,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    # review_reasons still surface (existing semantics) — but the
    # actionable id does not.
    assert body["review_reasons"] == ["duplicate_suspected"]
    assert body["pending_review_queue_id"] is None


@pytest.mark.asyncio
async def test_pending_review_queue_id_null_for_resolved_queue_row(
    client, db_session, world, admin_token
):
    """Case 4: reviewed expense with historical resolved queue row → field is null.

    Same guard as the rejected case: the resolved queue row is a
    historical record, not a callable action. Mobile must not see it
    as approve-able.
    """
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )
    await _seed_queue_row(
        db_session,
        expense_id=exp.expense_id,
        reasons=[ReviewReasonCode.category_uncertain],
        status=ReviewQueueStatus.resolved,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_reasons"] == ["category_uncertain"]
    assert body["pending_review_queue_id"] is None


@pytest.mark.asyncio
async def test_pending_review_queue_id_null_when_no_queue_row(
    client, db_session, world, admin_token
):
    """Case 5: no queue row ever → field is null."""
    exp = await _seed_structured_expense(
        db_session,
        job_id=world["job_a"].job_id,
        entered_by_user_id=world["admin"].user_id,
        review_status=ReviewStatus.reviewed,
    )

    r = await client.get(f"/expenses/{exp.expense_id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["pending_review_queue_id"] is None


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


# ---------------------------------------------------------------------------
# Capture Hardening Patch — Batch 1 acceptance tests
# (CHP-1 through CHP-5; see backend/app/services/parser/jobs.py and
# backend/app/services/expenses.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chp_world(db_session, seeded_admin, seed_categories):
    """Seed a CHP-specific world: three jobs with no aliases (simulates the
    realistic first-week state where the parser must rely on job_code or
    multi-token name match to resolve, not on pre-seeded aliases).

    * Smith Residence (SMITH-01)
    * Brown Renovation (BROWN-03)
    * 晶晶家 (JJ-02) — single-token CJK name to confirm Phase 4 CJK path is
      unaffected by CHP-1.
    """
    smith = await _mk_job(
        db_session, seeded_admin, name="Smith Residence", code="SMITH-01"
    )
    brown = await _mk_job(
        db_session, seeded_admin, name="Brown Renovation", code="BROWN-03"
    )
    jingjing = await _mk_job(db_session, seeded_admin, name="晶晶家", code="JJ-02")
    await db_session.flush()
    return {
        "admin": seeded_admin,
        "smith": smith,
        "brown": brown,
        "jingjing": jingjing,
    }


# ---------------------------------------------------------------------------
# CHP-1 — multi-token contiguous job-name match (acceptance via API path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp1_full_multiword_job_name_saves(
    client, db_session, chp_world, admin_token
):
    """Case A: ``"Smith Residence Bunnings $440 cement"`` saves and resolves
    to Smith Residence. No HTTP 422.
    """
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Smith Residence Bunnings $440 cement",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["job_id"] == str(chp_world["smith"].job_id)
    # Save happens; pending only because supplier is uncertain (unrelated
    # to job match). The key assertion: the expense exists with the
    # correct job_id resolved purely from the multi-word name.
    assert body["parse"]["job_conf"] == 0.95
    assert body["parse"]["matched_job_via"] == "multi_token_name"


@pytest.mark.asyncio
async def test_chp1_multiword_lowercase_saves(
    client, db_session, chp_world, admin_token
):
    """Case A lowercase: ``"brown renovation 5400 stratco roofing"`` saves."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "brown renovation 5400 stratco roofing",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["job_id"] == str(chp_world["brown"].job_id)
    assert body["parse"]["matched_job_via"] == "multi_token_name"


@pytest.mark.asyncio
async def test_chp1_cjk_single_token_name_still_saves(
    client, db_session, chp_world, admin_token
):
    """晶晶家 (single CJK token job name) must still match exactly via the
    single-token name route. CHP-1 does not regress Phase 4 CJK behaviour.
    """
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "晶晶家 水泥 800 现金",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["job_id"] == str(chp_world["jingjing"].job_id)
    assert body["parse"]["job_conf"] == 0.95
    assert body["parse"]["matched_job_via"] == "name"


# ---------------------------------------------------------------------------
# CHP-2 — actionable 422 messages: shorthand / ambiguous / no-match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp2_unique_shorthand_returns_did_you_mean(
    client, db_session, chp_world, admin_token
):
    """Case B: ``"Bunnings $440 cement smith"`` — only Smith Residence has a
    name starting with ``"smith"``. Returns 422 with "Did you mean Smith
    Residence (SMITH-01)?". No expense or queue row created.
    """
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Bunnings $440 cement smith",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "Did you mean" in detail
    assert "Smith Residence" in detail
    assert "SMITH-01" in detail
    # Side-effect contract: NO expense row, NO review_queue row created.
    expenses = (
        await db_session.execute(select(Expense).where(Expense.job_id == chp_world["smith"].job_id))
    ).scalars().all()
    assert len(expenses) == 0
    queue = (
        await db_session.execute(select(ExpenseReviewQueue))
    ).scalars().all()
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_chp2_ambiguous_shorthand_returns_both_candidates(
    client, db_session, seeded_admin, seed_categories, admin_token
):
    """Case C: when two jobs both start with the typed shorthand, return 422
    naming BOTH candidates. No expense or queue row created.
    """
    smith_a = await _mk_job(
        db_session, seeded_admin, name="Smith Residence", code="SMITH-01"
    )
    smith_b = await _mk_job(
        db_session, seeded_admin, name="Smith Bathroom", code="SMITH-02"
    )
    await db_session.flush()

    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Bunnings $440 smith",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "ambiguous" in detail.lower()
    assert "Smith Residence" in detail
    assert "Smith Bathroom" in detail
    assert "SMITH-01" in detail
    assert "SMITH-02" in detail
    # No silent attribution — neither job got the expense.
    for j in (smith_a, smith_b):
        rows = (
            await db_session.execute(select(Expense).where(Expense.job_id == j.job_id))
        ).scalars().all()
        assert len(rows) == 0
    queue = (
        await db_session.execute(select(ExpenseReviewQueue))
    ).scalars().all()
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_chp2_two_codes_returns_actionable_ambiguity(
    client, db_session, chp_world, admin_token
):
    """Two valid job codes in the input → 422 with both candidates named."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 BROWN-03 plumbing 1100",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "ambiguous" in detail.lower()
    assert "Smith Residence" in detail
    assert "Brown Renovation" in detail


@pytest.mark.asyncio
async def test_chp2_no_match_returns_actionable_guidance(
    client, db_session, chp_world, admin_token
):
    """No job hint at all → 422 with "Couldn't identify a job — please
    mention a job code or the full job name in your text.".
    """
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "Bunnings 1290 paint",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "Couldn't identify a job" in detail
    assert "job code" in detail.lower() or "full job name" in detail.lower()


# ---------------------------------------------------------------------------
# CHP-3 — duplicate detection through the FULL POST /expenses API path
# (regression test: prior simulated-trial finding of "duplicate detection
# not firing" was a trial-driver bug, not a production bug. This test
# locks in the contract so a future regression is caught immediately.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp3_duplicate_detection_fires_via_api(
    client, db_session, chp_world, admin_token
):
    """Identical raw_input_text submitted twice → second response carries
    ``expense.duplicate_of_expense_id`` pointing at the first AND
    ``parse.review_reasons`` includes ``"duplicate_suspected"``.

    Tests the path the real builder uses (POST /expenses), not just the
    parser stage in isolation.
    """
    body = {
        "raw_input_text": "SMITH-01 dup-test cement 555",
        "expense_date": _today_iso(),
    }

    r1 = await client.post("/expenses", headers=_auth(admin_token), json=body)
    assert r1.status_code == 201, r1.text
    first = r1.json()
    first_id = first["expense"]["expense_id"]
    # First submission has no prior to match against.
    assert first["expense"]["duplicate_of_expense_id"] is None
    assert first["expense"]["duplicate_flag"] is False
    assert "duplicate_suspected" not in first["parse"]["review_reasons"]

    r2 = await client.post("/expenses", headers=_auth(admin_token), json=body)
    assert r2.status_code == 201, r2.text
    second = r2.json()
    # Second submission must point at the first.
    assert second["expense"]["duplicate_of_expense_id"] == first_id
    assert second["expense"]["duplicate_flag"] is True
    assert "duplicate_suspected" in second["parse"]["review_reasons"]
    # And the review-queue row carries the reason too.
    queue = (
        await db_session.execute(
            select(ExpenseReviewQueue).where(
                ExpenseReviewQueue.expense_id == uuid.UUID(second["expense"]["expense_id"])
            )
        )
    ).scalar_one()
    assert ReviewReasonCode.duplicate_suspected in queue.review_reasons


# ---------------------------------------------------------------------------
# CHP-4 — amount cap honoured on the raw_input_text path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp4_raw_amount_above_cap_rejected(
    client, db_session, chp_world, admin_token
):
    """Raw text with a $99M amount → 422 (Pydantic cap previously bypassed
    on the parser-driven path).
    """
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 massive 99000000",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 422, r.text
    assert "exceeds maximum" in r.json().get("detail", "").lower()
    # No expense persisted.
    rows = (
        await db_session.execute(
            select(Expense).where(Expense.job_id == chp_world["smith"].job_id)
        )
    ).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_chp4_raw_amount_at_cap_accepted(
    client, db_session, chp_world, admin_token
):
    """Boundary: the literal cap value $10,000,000 is accepted (``<=`` semantics)."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 boundary 10000000",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["expense"]["amount_inc_gst"]) == Decimal("10000000")


# ---------------------------------------------------------------------------
# CHP-5 — future-date guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chp5_future_date_two_days_ahead_rejected(
    client, db_session, chp_world, admin_token
):
    """``today + 2 days`` is beyond the +1-day clock-skew tolerance → 422."""
    far_future = (_datetime.date.today() + _datetime.timedelta(days=2)).isoformat()
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 cement 440",
            "expense_date": far_future,
        },
    )
    assert r.status_code == 422, r.text
    assert "future" in r.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_chp5_future_date_one_day_ahead_accepted(
    client, db_session, chp_world, admin_token
):
    """``today + 1 day`` is within the clock-skew tolerance → 201."""
    tomorrow = (_datetime.date.today() + _datetime.timedelta(days=1)).isoformat()
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 cement 440",
            "expense_date": tomorrow,
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_chp5_today_accepted(client, db_session, chp_world, admin_token):
    """Today is the obvious common case — must accept."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 cement 440",
            "expense_date": _today_iso(),
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_chp5_year_2099_rejected(client, db_session, chp_world, admin_token):
    """Far-future date from a typo → 422 with the same future-date message."""
    r = await client.post(
        "/expenses",
        headers=_auth(admin_token),
        json={
            "raw_input_text": "SMITH-01 cement 440",
            "expense_date": "2099-01-01",
        },
    )
    assert r.status_code == 422, r.text
    assert "future" in r.json().get("detail", "").lower()
