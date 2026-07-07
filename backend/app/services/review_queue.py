"""Review-queue business logic for Phase 2 Task T-N.

HTTP-agnostic. Four operations drive the queue lifecycle:

1. :func:`list_open` — list queue rows, defaulting to ``status=open``.
2. :func:`get` — load one queue row with its expense (and any
   duplicate-of expense when set).
3. :func:`resolve` — ATOMIC: apply optional expense_patch, flip the
   expense to ``reviewed``, close the queue row, and write one
   :class:`ExpenseAuditLog` row that captures the status transition and
   every field the patch actually changed.
4. :func:`reject` — ATOMIC: flip the expense to ``rejected``, close the
   queue row, and write one :class:`ExpenseAuditLog` row for the
   transition.

Atomicity guarantee
-------------------
``get_db`` commits once per request on success and rolls back on any
exception, so the three DB mutations in :func:`resolve` / :func:`reject`
all commit together or not at all. No intermediate ``await db.commit()``
is performed here — that would break atomicity by splitting the work
into separately-committed transactions.

Audit rules
-----------
Per the Phase 2 plan, every ``review_status`` transition writes an
audit row, regardless of the actor. Both :func:`resolve` and
:func:`reject` always write exactly one audit row. The ``reason``
column is sourced from the request's ``notes`` field. The
``changed_fields`` shape mirrors T-M's convention:
``{"field_name": {"old": <serialisable>, "new": <serialisable>}}``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    Expense,
    ExpenseAuditLog,
    ExpenseReviewQueue,
    ReviewQueueStatus,
    ReviewStatus,
    Supplier,
    User,
)
from app.schemas.expense import ExpenseUpdate
from app.services.expenses import (
    _AUDITABLE_FIELDS,
    ExpenseValidationError,
    JobNotFoundForExpense,
    _coerce_audit_value,
    _compute_gst_split,
    _validate_job_active_for_reassign,
    _validate_save,
)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ReviewQueueNotFound(Exception):
    """Raised when a review_id doesn't resolve to a queue row."""

    def __init__(self, review_id: uuid.UUID):
        self.review_id = review_id
        super().__init__(f"Review queue entry {review_id} not found")


class ReviewQueueAlreadyClosed(Exception):
    """Raised when a resolve/reject is attempted on a non-open queue row."""

    def __init__(self, review_id: uuid.UUID, current_status: ReviewQueueStatus):
        self.review_id = review_id
        self.current_status = current_status
        super().__init__(f"Review queue entry {review_id} is already {current_status.value!r}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_queue_row_or_404(
    db: AsyncSession, review_id: uuid.UUID, *, for_update: bool = False
) -> ExpenseReviewQueue:
    """Load a queue row or raise 404.

    ``for_update=True`` takes a ``SELECT ... FOR UPDATE`` row lock so the
    resolve / reject transition is check-then-act *serialized* — two
    concurrent resolves/rejects on the same open row can no longer both
    pass the ``status == open`` check on stale snapshots and write two
    contradictory audit rows (audit R18). The read-only detail loader
    leaves this off.
    """
    # ``of`` restricts the lock to the queue row itself. A bare FOR UPDATE
    # would also target the eager LEFT-JOINed ``users`` row (resolved_by)
    # and Postgres rejects FOR UPDATE on the nullable side of an outer join.
    lock = {"of": ExpenseReviewQueue} if for_update else None
    row = await db.get(ExpenseReviewQueue, review_id, with_for_update=lock)
    if row is None:
        raise ReviewQueueNotFound(review_id)
    return row


def _require_expense(expense: Expense | None, expense_id: uuid.UUID) -> Expense:
    """Return ``expense`` or raise on the queue→expense FK invariant.

    The FK guarantees the expense exists; an explicit raise (rather than
    ``assert``, which ``python -O`` strips) turns a would-be invariant
    violation into a clear error instead of a later ``AttributeError``
    (audit R31).
    """
    if expense is None:
        raise RuntimeError(f"Review queue references missing expense {expense_id}")
    return expense


async def _validate_patch_fk_refs(
    db: AsyncSession,
    *,
    supplier_id: uuid.UUID | None,
    category_id: uuid.UUID | None,
) -> None:
    """Validate any patched supplier/category FK points at a real row.

    Raises :class:`ValueError` which the HTTP layer maps to 422. Using a
    Python-level lookup here (rather than relying on the DB
    ``IntegrityError``) gives the caller a clean validation response.
    """
    if supplier_id is not None:
        sup = await db.get(Supplier, supplier_id)
        if sup is None:
            raise ValueError(f"Supplier {supplier_id} not found")
    if category_id is not None:
        cat = await db.get(Category, category_id)
        if cat is None:
            raise ValueError(f"Category {category_id} not found")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def list_open(
    db: AsyncSession,
    *,
    status: ReviewQueueStatus = ReviewQueueStatus.open,
) -> list[ExpenseReviewQueue]:
    """List review-queue rows ordered by ``opened_at`` ASC.

    Default is ``status=open``. Admin-only — enforcement is at the API
    boundary.
    """
    stmt = (
        select(ExpenseReviewQueue)
        .where(ExpenseReviewQueue.status == status)
        .order_by(ExpenseReviewQueue.opened_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Get detail
# ---------------------------------------------------------------------------


async def get(
    db: AsyncSession,
    *,
    review_id: uuid.UUID,
) -> tuple[ExpenseReviewQueue, Expense, Expense | None]:
    """Load a queue row + its expense + the duplicate-of expense if any.

    Eager-loads ``supplier`` + ``category`` + ``entered_by`` on both
    expenses so the caller can serialise :class:`ExpenseDetailPublic`
    without additional round trips.
    """
    row = await _get_queue_row_or_404(db, review_id)

    # The expense model already uses ``lazy="joined"`` for supplier,
    # category, and entered_by — so a plain ``db.get`` suffices to
    # satisfy ExpenseDetailPublic. We still issue an explicit select on
    # the duplicate-of row so it's eagerly populated.
    expense = _require_expense(await db.get(Expense, row.expense_id), row.expense_id)

    duplicate_of: Expense | None = None
    if expense.duplicate_of_expense_id is not None:
        duplicate_of = await db.get(Expense, expense.duplicate_of_expense_id)

    return row, expense, duplicate_of


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _write_audit(
    db: AsyncSession,
    *,
    expense_id: uuid.UUID,
    admin: User,
    changed_fields: dict[str, dict[str, Any]],
    reason: str | None,
) -> None:
    """Stage a single :class:`ExpenseAuditLog` row on the session.

    Does NOT commit or flush — atomicity is owned by ``get_db``. The
    shape of ``changed_fields`` is the same convention T-M uses:
    ``{"field_name": {"old": ..., "new": ...}}``.
    """
    audit = ExpenseAuditLog(
        audit_id=uuid.uuid4(),
        expense_id=expense_id,
        edited_by_user_id=admin.user_id,
        changed_fields=changed_fields,
        reason=reason,
    )
    db.add(audit)


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------


async def resolve(
    db: AsyncSession,
    *,
    admin: User,
    review_id: uuid.UUID,
    expense_patch: ExpenseUpdate | None,
    notes: str | None,
) -> None:
    """Approve a queue entry: patch + transition + close + audit, atomically.

    Raises :class:`ReviewQueueNotFound` if the queue row doesn't exist,
    :class:`ReviewQueueAlreadyClosed` if its status is not ``open``, or
    :class:`ValueError` if ``expense_patch`` references a bad FK.

    All three DB mutations (expense update, queue close, audit insert)
    are staged on the session and commit together via ``get_db``. Any
    raised exception triggers ``get_db``'s rollback path, so the
    transaction aborts and no partial state persists.
    """
    queue_row = await _get_queue_row_or_404(db, review_id, for_update=True)
    if queue_row.status != ReviewQueueStatus.open:
        raise ReviewQueueAlreadyClosed(review_id, queue_row.status)

    expense = _require_expense(
        await db.get(Expense, queue_row.expense_id), queue_row.expense_id
    )

    # Snapshot pre-image for audit diff computation.
    pre_image: dict[str, Any] = {field: getattr(expense, field) for field in _AUDITABLE_FIELDS}
    pre_status = expense.review_status

    # Apply the optional expense_patch. We re-use the T-M _AUDITABLE_FIELDS
    # tuple so the behaviour is identical to PATCH /expenses/{id}: every
    # field the caller explicitly set is written; unset fields are left
    # alone.
    patch_set: set[str] = set()
    if expense_patch is not None:
        patch_set = set(expense_patch.model_fields_set)
        # ``reason`` on ExpenseUpdate is the audit note for PATCH /expenses;
        # here it's not used (the queue-resolve ``notes`` field replaces it).
        patch_set.discard("reason")
        # ``review_status`` via expense_patch is ignored — resolving the
        # queue row by definition transitions to ``reviewed``; letting the
        # caller override that here would produce a contradictory audit row.
        patch_set.discard("review_status")

        # Pre-check any FK references for a clean 422.
        await _validate_patch_fk_refs(
            db,
            supplier_id=expense_patch.supplier_id if "supplier_id" in patch_set else None,
            category_id=expense_patch.category_id if "category_id" in patch_set else None,
        )

        # A1 (Option A): job reassignment rules on the resolve path. All
        # raises are ValueError so the resolve route's ``except ValueError
        # -> 422`` maps them (and we never reach the apply loop / status
        # flip below, so the expense + queue row stay untouched).
        if "job_id" in patch_set:
            # Never allow clearing an expense's job (deliberate contract;
            # raised here rather than leaving it to _validate_save, which
            # would surface as a 500 on this route).
            if expense_patch.job_id is None:
                raise ValueError(
                    "An expense must always have a job; job_id cannot be cleared"
                )
            # Reassignment must target an ACTIVE job (resolve is admin-only
            # via the route, so no role check here).
            if expense_patch.job_id != expense.job_id:
                try:
                    await _validate_job_active_for_reassign(db, expense_patch.job_id)
                except (JobNotFoundForExpense, ExpenseValidationError) as exc:
                    raise ValueError(str(exc)) from exc

        for field in _AUDITABLE_FIELDS:
            if field in patch_set:
                setattr(expense, field, getattr(expense_patch, field))

        # Re-reconcile the GST split whenever ANY money field moves on the
        # reviewer path (audit X-1 / X-2). The previous hardcoded 1/11 divisor
        # ignored payment_method (giving cash rows a phantom GST) and skipped
        # lone-component patches. Delegating to the shared reconciler makes
        # this path payment-aware and consistent with PATCH /expenses.
        money_fields = {"amount_inc_gst", "payment_method", "amount_ex_gst", "gst_amount"}
        if patch_set & money_fields:
            ex_override = expense.amount_ex_gst if "amount_ex_gst" in patch_set else None
            gst_override = expense.gst_amount if "gst_amount" in patch_set else None
            expense.amount_ex_gst, expense.gst_amount = _compute_gst_split(
                expense.amount_inc_gst,
                ex_override,
                gst_override,
                expense.payment_method,
            )

        # Re-run save-time validation on the post-patch state.
        _validate_save(
            job_id=expense.job_id,
            supplier_id=expense.supplier_id,
            amount_inc_gst=expense.amount_inc_gst,
            expense_date=expense.expense_date,
            expense_type=expense.expense_type,
            description=expense.description,
        )

    # Flip the expense to reviewed.
    expense.review_status = ReviewStatus.reviewed

    # Close the queue row.
    now = datetime.now(UTC)
    queue_row.status = ReviewQueueStatus.resolved
    queue_row.resolved_by_user_id = admin.user_id
    queue_row.resolved_at = now
    queue_row.resolution_notes = notes

    # Flush so any DB-level constraint violation (bad FK, null not-null,
    # etc.) surfaces here as an exception that get_db will roll back.
    await db.flush()

    # Build the audit diff. Always includes the status transition; also
    # every patched field that actually changed.
    changed_fields: dict[str, dict[str, Any]] = {
        "review_status": {
            "old": _coerce_audit_value(pre_status),
            "new": _coerce_audit_value(ReviewStatus.reviewed),
        },
    }
    for field in _AUDITABLE_FIELDS:
        if field == "review_status":
            continue
        old = pre_image[field]
        new = getattr(expense, field)
        if old != new:
            changed_fields[field] = {
                "old": _coerce_audit_value(old),
                "new": _coerce_audit_value(new),
            }

    _write_audit(
        db,
        expense_id=expense.expense_id,
        admin=admin,
        changed_fields=changed_fields,
        reason=notes,
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


async def reject(
    db: AsyncSession,
    *,
    admin: User,
    review_id: uuid.UUID,
    notes: str | None,
) -> None:
    """Reject a queue entry: transition + close + audit, atomically.

    Raises :class:`ReviewQueueNotFound` if the queue row doesn't exist,
    :class:`ReviewQueueAlreadyClosed` if its status is not ``open``.

    All three DB mutations (expense update, queue close, audit insert)
    are staged on the session and commit together via ``get_db``.
    """
    queue_row = await _get_queue_row_or_404(db, review_id, for_update=True)
    if queue_row.status != ReviewQueueStatus.open:
        raise ReviewQueueAlreadyClosed(review_id, queue_row.status)

    expense = _require_expense(
        await db.get(Expense, queue_row.expense_id), queue_row.expense_id
    )
    pre_status = expense.review_status

    expense.review_status = ReviewStatus.rejected

    now = datetime.now(UTC)
    queue_row.status = ReviewQueueStatus.rejected
    queue_row.resolved_by_user_id = admin.user_id
    queue_row.resolved_at = now
    queue_row.resolution_notes = notes

    await db.flush()

    _write_audit(
        db,
        expense_id=expense.expense_id,
        admin=admin,
        changed_fields={
            "review_status": {
                "old": _coerce_audit_value(pre_status),
                "new": _coerce_audit_value(ReviewStatus.rejected),
            },
        },
        reason=notes,
    )
    await db.flush()
