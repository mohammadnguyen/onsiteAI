"""Expense business logic for Phase 2 Task T-M.

HTTP-agnostic. Each function takes an :class:`AsyncSession` plus typed
inputs and either returns persisted model rows or raises one of the
domain exceptions defined at the top of this module. The HTTP layer
(``app/api/expenses.py``) is the only caller and is responsible for
mapping these exceptions onto the correct status codes.

Core responsibilities of the service:

1. **Create** — if ``raw_input_text`` was supplied, run
   :func:`app.services.parser.parse`, merge any explicit structured
   overrides on top of the parser draft, validate, persist
   :class:`Expense`, and — when the parser produced review reasons —
   also insert an :class:`ExpenseReviewQueue` row.
2. **Parse preview** — run the parser, build a
   :class:`ParsePreview` (draft + diagnostics) and return it. Does not
   persist.
3. **List / get** — enforces contributor ownership (contributors only
   see their own rows).
4. **Update** — RBAC + review-status rules:
    * contributor may only edit own pending rows
    * admin may edit any; edits on rows whose pre-update
      ``review_status`` is ``reviewed`` (or that transition
      ``review_status``) always write an :class:`ExpenseAuditLog` row
      recording the field-level diff and the optional admin reason.
    * admin edits on ``pending`` rows do not write audit rows (part of
      the review workflow).
    * contributor edits never write audit rows.
5. **Delete** — admin-only soft delete (sets
   ``review_status = rejected`` + closes any open queue row + writes
   an audit row recording the transition).
6. **Get audit** — admin-only list of audit rows for an expense.

Parser integration is strictly delegated — no parser logic is
re-implemented here. See :mod:`app.services.parser` for the orchestrator.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    Expense,
    ExpenseAuditLog,
    ExpenseReviewQueue,
    ExpenseType,
    Job,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewStatus,
    Supplier,
    User,
    UserRole,
)
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseUpdate,
    ParseDiagnostics,
    ParsePreview,
)
from app.services.parser import LLMParser, ParseResult, parse

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ExpenseNotFound(Exception):
    """Raised when an expense_id doesn't resolve to a persisted row."""

    def __init__(self, expense_id: uuid.UUID):
        self.expense_id = expense_id
        super().__init__(f"Expense {expense_id} not found")


class EditForbidden(Exception):
    """Raised when a contributor edits a row they don't own or a reviewed row."""

    def __init__(self, detail: str = "Edit forbidden"):
        self.detail = detail
        super().__init__(detail)


class DeleteForbidden(Exception):
    """Raised when a contributor tries to delete an expense."""

    def __init__(self, detail: str = "Delete forbidden"):
        self.detail = detail
        super().__init__(detail)


class ExpenseValidationError(Exception):
    """Raised on save-time validation errors (amount, job, supplier, date)."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class JobNotFoundForExpense(Exception):
    """Raised when a structured ``job_id`` doesn't resolve to a job row."""

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_GST_DIVISOR = Decimal("1.1")
_MAX_PAST_YEARS = 5


def _diagnostics_from_result(result: ParseResult) -> ParseDiagnostics:
    """Fold a :class:`ParseResult` into the wire-shape diagnostics block."""
    p = result.partial
    return ParseDiagnostics(
        amount_conf=p.amount_conf,
        job_conf=p.job_conf,
        supplier_conf=p.supplier_conf,
        category_conf=p.category_conf,
        unsupported_currency=p.unsupported_currency,
        review_reasons=list(result.review_reasons),
        ambiguous_job_matches=list(result.ambiguous_job_matches),
        ambiguous_supplier_matches=list(result.ambiguous_supplier_matches),
        matched_job_via=result.matched_job_via,
        matched_supplier_via=result.matched_supplier_via,
        candidate_supplier_name=p.candidate_supplier_name,
        duplicate_of_expense_id=p.duplicate_of_expense_id,
        source_per_field=dict(p.source_per_field),
    )


def _fields_set_by_caller(payload: ExpenseCreate) -> set[str]:
    """Return the set of field names the caller explicitly set on ``payload``.

    Pydantic tracks explicit input via ``model_fields_set`` — defaults
    are excluded. That's what we want for "structured override wins"
    semantics: only fields the caller actually typed should overwrite
    the parser draft.
    """
    return set(payload.model_fields_set)


def _merge_parse_with_overrides(
    parsed: ParseResult,
    overrides: ExpenseCreate,
    caller_set: set[str],
) -> dict[str, Any]:
    """Combine parser output with explicit structured overrides.

    Returns a dict of the final field values to write onto a new
    :class:`Expense`. Any field the caller set explicitly on
    ``overrides`` wins; any field they left unset is taken from the
    parser's draft. ``raw_input_text`` / ``expense_type`` /
    ``expense_date`` / ``payment_method`` have sensible defaults
    sourced from the parser when the caller didn't set them.
    """
    p = parsed.partial

    draft: dict[str, Any] = {
        "job_id": p.job_id,
        "supplier_id": p.supplier_id,
        "amount_inc_gst": p.amount_value,
        "amount_ex_gst": None,
        "gst_amount": None,
        "payment_method": p.payment_method,
        "expense_type": p.expense_type,
        "category_id": p.category_id,
        "description": p.description,
    }

    # Fields the caller explicitly set override parser output.
    for key in (
        "job_id",
        "supplier_id",
        "amount_inc_gst",
        "amount_ex_gst",
        "gst_amount",
        "payment_method",
        "expense_type",
        "category_id",
        "description",
    ):
        if key in caller_set:
            draft[key] = getattr(overrides, key)

    return draft


def _compute_gst_split(
    amount_inc: Decimal,
    amount_ex: Decimal | None,
    gst: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Derive the ex-GST / GST pair from the inclusive total when unset.

    Mirrors the ``_compute_gst_split`` listener on
    :class:`~app.models.expense.Expense` but runs eagerly in Python so
    the service can write all three columns in a single INSERT (the
    listener covers the case where the caller only supplied
    ``amount_inc_gst``, but writing the split ourselves also lets us
    return consistent values in the wire response without waiting for
    a ``db.refresh`` round trip).
    """
    if amount_ex is None and gst is None:
        ex = (amount_inc / _GST_DIVISOR).quantize(Decimal("0.01"))
        return ex, amount_inc - ex
    if amount_ex is None:
        return amount_inc - gst, gst  # type: ignore[operator]
    if gst is None:
        return amount_ex, amount_inc - amount_ex
    return amount_ex, gst


async def _get_expense_or_404(db: AsyncSession, expense_id: uuid.UUID) -> Expense:
    expense = await db.get(Expense, expense_id)
    if expense is None:
        raise ExpenseNotFound(expense_id)
    return expense


async def _get_open_queue_row(db: AsyncSession, expense_id: uuid.UUID) -> ExpenseReviewQueue | None:
    """Return the single open review-queue row for ``expense_id``, if any."""
    stmt = select(ExpenseReviewQueue).where(
        ExpenseReviewQueue.expense_id == expense_id,
        ExpenseReviewQueue.status == ReviewQueueStatus.open,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def _owns(expense: Expense, user: User) -> bool:
    return expense.entered_by_user_id == user.user_id


async def _validate_fk_refs(
    db: AsyncSession,
    *,
    job_id: uuid.UUID | None,
    supplier_id: uuid.UUID | None,
    category_id: uuid.UUID | None,
) -> None:
    """Pre-check supplier + category FKs for a friendly 422 ahead of IntegrityError.

    Job lookup is handled by the caller (raises
    :class:`JobNotFoundForExpense` so the HTTP layer can surface a
    specific 422 detail).
    """
    if supplier_id is not None:
        sup = await db.get(Supplier, supplier_id)
        if sup is None:
            raise ExpenseValidationError(f"Supplier {supplier_id} not found")
    if category_id is not None:
        cat = await db.get(Category, category_id)
        if cat is None:
            raise ExpenseValidationError(f"Category {category_id} not found")


def _validate_save(
    *,
    job_id: uuid.UUID | None,
    supplier_id: uuid.UUID | None,
    amount_inc_gst: Decimal | None,
    expense_date: date,
    expense_type: ExpenseType,
    description: str | None,
) -> None:
    """Run save-time validation rules. Raises :class:`ExpenseValidationError`."""
    if amount_inc_gst is None:
        raise ExpenseValidationError("Amount is required")
    if amount_inc_gst <= 0:
        raise ExpenseValidationError("Amount must be greater than zero")
    if job_id is None:
        raise ExpenseValidationError("Job is required")

    # supplier-expense rows must have a supplier OR a description so
    # the row has at least one human-readable anchor beyond the amount.
    if (
        expense_type == ExpenseType.supplier_expense
        and supplier_id is None
        and not (description and description.strip())
    ):
        raise ExpenseValidationError("Supplier or description is required for supplier expenses")

    # Sanity: reject expense_date more than 5 years in the past.
    cutoff = date.today() - timedelta(days=365 * _MAX_PAST_YEARS)
    if expense_date < cutoff:
        raise ExpenseValidationError("Expense date is more than 5 years in the past")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_expense(
    db: AsyncSession,
    *,
    entered_by: User,
    payload: ExpenseCreate,
    llm_parser: LLMParser | None = None,
) -> tuple[Expense, ParseDiagnostics | None]:
    """Persist a new :class:`Expense` (optionally running the parser first).

    Returns ``(expense, diagnostics)``. ``diagnostics`` is ``None`` iff
    the caller did not supply ``raw_input_text`` (structured-only
    submission).
    """
    caller_set = _fields_set_by_caller(payload)
    expense_date = payload.expense_date or date.today()
    expense_type = payload.expense_type
    parse_result: ParseResult | None = None
    diagnostics: ParseDiagnostics | None = None

    if payload.raw_input_text is not None and payload.raw_input_text.strip():
        parse_result = await parse(
            raw_text=payload.raw_input_text,
            db=db,
            entered_by=entered_by,
            expense_date=expense_date,
            expense_type=expense_type,
            llm_parser=llm_parser,
        )
        diagnostics = _diagnostics_from_result(parse_result)
        merged = _merge_parse_with_overrides(parse_result, payload, caller_set)
    else:
        # Structured-only submission: no parser, pick caller fields verbatim.
        merged = {
            "job_id": payload.job_id,
            "supplier_id": payload.supplier_id,
            "amount_inc_gst": payload.amount_inc_gst,
            "amount_ex_gst": payload.amount_ex_gst,
            "gst_amount": payload.gst_amount,
            "payment_method": payload.payment_method,
            "expense_type": expense_type,
            "category_id": payload.category_id,
            "description": payload.description,
        }

    # Finalise validation + FK pre-checks.
    _validate_save(
        job_id=merged["job_id"],
        supplier_id=merged["supplier_id"],
        amount_inc_gst=merged["amount_inc_gst"],
        expense_date=expense_date,
        expense_type=merged["expense_type"],
        description=merged["description"],
    )
    # Confirm the job id refers to a real row.
    job = await db.get(Job, merged["job_id"])
    if job is None:
        raise JobNotFoundForExpense(merged["job_id"])
    await _validate_fk_refs(
        db,
        job_id=merged["job_id"],
        supplier_id=merged["supplier_id"],
        category_id=merged["category_id"],
    )

    # Compute GST split up front (the model listener would do this at
    # flush time; doing it here keeps the wire response immediately
    # consistent).
    amount_ex, gst = _compute_gst_split(
        merged["amount_inc_gst"], merged["amount_ex_gst"], merged["gst_amount"]
    )

    # Pick review_status + confidence_score from the parse result, if any.
    review_status = (
        parse_result.review_status if parse_result is not None else ReviewStatus.reviewed
    )
    duplicate_flag = parse_result.partial.duplicate_flag if parse_result is not None else False
    duplicate_of_expense_id = (
        parse_result.partial.duplicate_of_expense_id if parse_result is not None else None
    )
    confidence_score: Decimal | None = None
    if parse_result is not None:
        # Use the minimum of primary confidences (amount, job, supplier)
        # as a single rolled-up score for UI display. Clamp to two
        # decimal places for the ``Numeric(3, 2)`` column.
        primary = [
            parse_result.partial.amount_conf,
            parse_result.partial.job_conf,
            parse_result.partial.supplier_conf,
        ]
        confidence_score = Decimal(str(round(min(primary), 2)))

    expense = Expense(
        expense_id=uuid.uuid4(),
        job_id=merged["job_id"],
        supplier_id=merged["supplier_id"],
        entered_by_user_id=entered_by.user_id,
        expense_type=merged["expense_type"],
        raw_input_text=payload.raw_input_text,
        description=merged["description"],
        amount_inc_gst=merged["amount_inc_gst"],
        amount_ex_gst=amount_ex,
        gst_amount=gst,
        payment_method=merged["payment_method"],
        expense_date=expense_date,
        category_id=merged["category_id"],
        review_status=review_status,
        receipt_status=payload.receipt_status,
        confidence_score=confidence_score,
        duplicate_flag=duplicate_flag,
        duplicate_of_expense_id=duplicate_of_expense_id,
        notes=payload.notes,
    )
    db.add(expense)
    await db.flush()

    # Enqueue a review-queue row if the parser produced any reasons.
    if parse_result is not None and parse_result.review_reasons:
        queue_row = ExpenseReviewQueue(
            review_id=uuid.uuid4(),
            expense_id=expense.expense_id,
            review_reasons=list(parse_result.review_reasons),
            status=ReviewQueueStatus.open,
        )
        db.add(queue_row)
        await db.flush()

    return expense, diagnostics


# ---------------------------------------------------------------------------
# Parse preview
# ---------------------------------------------------------------------------


async def preview_parse(
    db: AsyncSession,
    *,
    entered_by: User,
    raw_text: str,
    expense_date: date | None = None,
    expense_type: ExpenseType = ExpenseType.supplier_expense,
    llm_parser: LLMParser | None = None,
) -> ParsePreview:
    """Run the parser and return a :class:`ParsePreview`. Does NOT persist."""
    when = expense_date or date.today()
    result = await parse(
        raw_text=raw_text,
        db=db,
        entered_by=entered_by,
        expense_date=when,
        expense_type=expense_type,
        llm_parser=llm_parser,
    )

    # Build a best-guess ExpenseCreate draft from the parser output.
    # Not persisted — just a structured "here's what we'd write".
    p = result.partial
    draft = ExpenseCreate(
        raw_input_text=raw_text,
        job_id=p.job_id,
        supplier_id=p.supplier_id,
        expense_type=p.expense_type,
        amount_inc_gst=p.amount_value,
        payment_method=p.payment_method,
        expense_date=when,
        category_id=p.category_id,
        description=p.description,
    )

    # Discard the unused asdict path — it's imported for future parity
    # with the orchestrator but not needed for the preview serialisation.
    _ = asdict  # noqa: F841

    return ParsePreview(
        draft=draft,
        diagnostics=_diagnostics_from_result(result),
    )


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


async def list_expenses(
    db: AsyncSession,
    *,
    current_user: User,
    mine: bool,
    job_id: uuid.UUID | None = None,
    status: ReviewStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    receipt_status: ReceiptStatus | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[list[Expense], str | None]:
    """List expenses (contributors always restricted to their own rows).

    Phase 2 returns a simple list with an opaque ``next_cursor``; when
    fewer than ``limit`` rows match the cursor is ``None``. Full
    pagination (keyset) is left for a later task — this keeps Phase 2
    tests straightforward while still allowing clients to tell "there
    may be more".
    """
    stmt = select(Expense).order_by(Expense.expense_date.desc(), Expense.created_at.desc())

    # Contributors never see other users' expenses, regardless of
    # ``mine``. Admins see everyone by default; ``mine=True`` restricts
    # to their own rows.
    if not _is_admin(current_user) or mine:
        stmt = stmt.where(Expense.entered_by_user_id == current_user.user_id)

    if job_id is not None:
        stmt = stmt.where(Expense.job_id == job_id)
    if status is not None:
        stmt = stmt.where(Expense.review_status == status)
    if from_date is not None:
        stmt = stmt.where(Expense.expense_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Expense.expense_date <= to_date)
    if receipt_status is not None:
        stmt = stmt.where(Expense.receipt_status == receipt_status)

    stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        # Cursor payload is intentionally opaque. Phase 2 only needs
        # clients to know "there are more rows"; future tasks can wire
        # up real keyset pagination.
        next_cursor = rows[-1].expense_id.hex if rows else None

    return rows, next_cursor


async def get_expense(
    db: AsyncSession,
    *,
    current_user: User,
    expense_id: uuid.UUID,
) -> Expense:
    """Fetch one expense, enforcing contributor ownership."""
    expense = await _get_expense_or_404(db, expense_id)
    if not _is_admin(current_user) and not _owns(expense, current_user):
        raise EditForbidden("Not your expense")
    return expense


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


# Columns an audit row diff may cover. ``review_status`` transitions
# always write audit rows; the other fields only write audit rows when
# an admin touches a reviewed row.
_AUDITABLE_FIELDS: tuple[str, ...] = (
    "supplier_id",
    "expense_type",
    "amount_inc_gst",
    "amount_ex_gst",
    "gst_amount",
    "payment_method",
    "expense_date",
    "category_id",
    "description",
    "notes",
    "receipt_status",
    "review_status",
)


def _coerce_audit_value(value: Any) -> Any:
    """Convert a value into a JSON-serialisable form for JSONB audit rows."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value


async def update_expense(
    db: AsyncSession,
    *,
    current_user: User,
    expense_id: uuid.UUID,
    patch: ExpenseUpdate,
) -> Expense:
    """Apply a partial update to an expense, enforcing RBAC + audit rules."""
    expense = await _get_expense_or_404(db, expense_id)
    patch_set = set(patch.model_fields_set)
    # ``reason`` is audit-only, never a persisted column.
    patch_set.discard("reason")

    is_admin = _is_admin(current_user)
    owns = _owns(expense, current_user)
    pre_status = expense.review_status

    if not is_admin:
        if not owns:
            raise EditForbidden("Not your expense")
        if pre_status != ReviewStatus.pending:
            raise EditForbidden("Only pending expenses can be edited")
        # Contributors may not set review_status themselves.
        if "review_status" in patch_set:
            raise EditForbidden("Contributors cannot change review status")

    # Pre-check any new FK references for a clean 422.
    if "supplier_id" in patch_set or "category_id" in patch_set:
        await _validate_fk_refs(
            db,
            job_id=expense.job_id,
            supplier_id=patch.supplier_id if "supplier_id" in patch_set else expense.supplier_id,
            category_id=patch.category_id if "category_id" in patch_set else expense.category_id,
        )

    # Snapshot the pre-image for every auditable field so we can record
    # the diff after the update is applied.
    pre_image: dict[str, Any] = {field: getattr(expense, field) for field in _AUDITABLE_FIELDS}

    # Apply the patch.
    for field in _AUDITABLE_FIELDS:
        if field in patch_set:
            setattr(expense, field, getattr(patch, field))

    # If amount_inc_gst changed AND neither of the split components were
    # explicitly set in the patch, recompute the split.
    if (
        "amount_inc_gst" in patch_set
        and "amount_ex_gst" not in patch_set
        and "gst_amount" not in patch_set
    ):
        ex = (expense.amount_inc_gst / _GST_DIVISOR).quantize(Decimal("0.01"))
        expense.amount_ex_gst = ex
        expense.gst_amount = expense.amount_inc_gst - ex

    # Re-validate the post-update expense state.
    _validate_save(
        job_id=expense.job_id,
        supplier_id=expense.supplier_id,
        amount_inc_gst=expense.amount_inc_gst,
        expense_date=expense.expense_date,
        expense_type=expense.expense_type,
        description=expense.description,
    )

    await db.flush()

    # Decide whether to write an audit row.
    # - Contributor edits: never.
    # - Admin edits on a pre-update ``pending`` row with no status
    #   transition: no audit row (part of the review workflow).
    # - Admin edits on a pre-update ``reviewed`` row: always.
    # - Any ``review_status`` transition: always.
    status_changed = "review_status" in patch_set and expense.review_status != pre_status
    must_audit = is_admin and (pre_status != ReviewStatus.pending or status_changed)

    if must_audit:
        changed_fields: dict[str, dict[str, Any]] = {}
        for field in _AUDITABLE_FIELDS:
            old = pre_image[field]
            new = getattr(expense, field)
            if old != new:
                changed_fields[field] = {
                    "old": _coerce_audit_value(old),
                    "new": _coerce_audit_value(new),
                }
        # Only write an audit row when the edit actually changed
        # something. An empty patch on a reviewed row is a no-op.
        if changed_fields:
            audit = ExpenseAuditLog(
                audit_id=uuid.uuid4(),
                expense_id=expense.expense_id,
                edited_by_user_id=current_user.user_id,
                changed_fields=changed_fields,
                reason=patch.reason,
            )
            db.add(audit)
            await db.flush()

    return expense


# ---------------------------------------------------------------------------
# Delete (admin-only soft delete)
# ---------------------------------------------------------------------------


async def delete_expense(
    db: AsyncSession,
    *,
    admin: User,
    expense_id: uuid.UUID,
    reason: str | None,
) -> None:
    """Soft-delete an expense: sets ``review_status = rejected`` + audits.

    Also closes any open :class:`ExpenseReviewQueue` row by setting its
    ``status`` to ``rejected``. Contributors are rejected at the HTTP
    layer before this function is invoked; if a non-admin somehow
    reaches it, raises :class:`DeleteForbidden`.
    """
    if not _is_admin(admin):
        raise DeleteForbidden()

    expense = await _get_expense_or_404(db, expense_id)
    pre_status = expense.review_status

    # Already rejected: no-op. Idempotency here keeps retries safe.
    if pre_status == ReviewStatus.rejected:
        return

    expense.review_status = ReviewStatus.rejected
    await db.flush()

    # Close any open queue row.
    queue_row = await _get_open_queue_row(db, expense_id)
    if queue_row is not None:
        queue_row.status = ReviewQueueStatus.rejected
        queue_row.resolved_by_user_id = admin.user_id
        from datetime import UTC, datetime

        queue_row.resolved_at = datetime.now(UTC)
        await db.flush()

    # Audit the transition.
    audit = ExpenseAuditLog(
        audit_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        edited_by_user_id=admin.user_id,
        changed_fields={
            "review_status": {
                "old": _coerce_audit_value(pre_status),
                "new": _coerce_audit_value(ReviewStatus.rejected),
            },
        },
        reason=reason,
    )
    db.add(audit)
    await db.flush()


# ---------------------------------------------------------------------------
# Audit trail accessor
# ---------------------------------------------------------------------------


async def get_audit(
    db: AsyncSession,
    *,
    admin: User,
    expense_id: uuid.UUID,
) -> list[ExpenseAuditLog]:
    """Return the audit rows for an expense, newest first. Admin-only."""
    if not _is_admin(admin):
        raise DeleteForbidden("Admin only")
    # Confirm the expense exists so callers get a clean 404.
    _ = await _get_expense_or_404(db, expense_id)

    stmt = (
        select(ExpenseAuditLog)
        .where(ExpenseAuditLog.expense_id == expense_id)
        .order_by(ExpenseAuditLog.edited_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())
