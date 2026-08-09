"""Shared expense write / audit / GST-split core.

Neutral primitives used by BOTH the expense-edit path
(:mod:`app.services.expenses`) and the review-resolve path
(:mod:`app.services.review_queue`). Extracted so neither service reaches
into the other's private module namespace and the
apply / GST-reconcile / audit-diff logic lives in exactly one place.

Import direction is one-way: this module depends only on ``models`` (and,
transitively, nothing in the service layer). ``expenses`` and
``review_queue`` import from *here*; this module imports neither, so there
is no cycle.

Behaviour is IDENTICAL to the previous in-place code — this is a pure
extraction. Audit GATING (which edits write a row) and the
``review_status`` transition stay with each caller; only the mechanical
snapshot / money-reconcile / field-diff primitives live here.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense, ExpenseType, Job, JobStatus, PaymentMethod
from app.models.expense import GstSplitError, reconcile_gst_split

# ---------------------------------------------------------------------------
# Domain exceptions (shared by the edit + resolve paths)
# ---------------------------------------------------------------------------


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
# Validation bounds
# ---------------------------------------------------------------------------


_MAX_PAST_YEARS = 5

# CHP-4: hard upper bound on persisted amounts (matches the
# `ExpenseCreate.amount_inc_gst` Pydantic field cap; restated here so
# the raw_input_text path doesn't bypass it). $10M is well above any
# legitimate residential-builder line item; anything bigger is a
# fat-finger and should be rejected at the API edge before it pollutes
# job rollups.
_MAX_AMOUNT_INC_GST = Decimal("10000000")

# CHP-5: tolerance for clock-skew between a contributor's phone and
# the server. Today + this many days is the latest expense_date we
# accept; anything beyond is a back-dated typo or a future-dated
# entry-error and should be rejected.
_FUTURE_DATE_TOLERANCE_DAYS = 1


# ---------------------------------------------------------------------------
# GST split
# ---------------------------------------------------------------------------


def compute_gst_split(
    amount_inc: Decimal,
    amount_ex: Decimal | None,
    gst: Decimal | None,
    payment_method: PaymentMethod | None,
) -> tuple[Decimal, Decimal]:
    """Derive the ex-GST / GST pair from the inclusive total when unset.

    Mirrors the ``_compute_gst_split`` listener on
    :class:`~app.models.expense.Expense` but runs eagerly in Python so
    the service can write all three columns in a single INSERT and
    return consistent values in the wire response without waiting for
    a ``db.refresh`` round trip.

    The payment-method-aware rule and the ``inc = ex + gst`` invariant live
    in :func:`app.models.expense.reconcile_gst_split`; this thin wrapper only
    maps its :class:`GstSplitError` onto the service's
    :class:`ExpenseValidationError` so an inconsistent caller-supplied triple
    (audit B-1) surfaces as a 422, not a 500.
    """
    try:
        return reconcile_gst_split(amount_inc, amount_ex, gst, payment_method)
    except GstSplitError as exc:
        raise ExpenseValidationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Save-time validation
# ---------------------------------------------------------------------------


def validate_save(
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
    # CHP-4: enforce the upper-bound on the parser-driven path too.
    # The `ExpenseCreate.amount_inc_gst` Pydantic field already caps
    # caller-supplied structured amounts at $10M, but a parser-derived
    # amount comes via the merge dict and bypasses the field validator.
    # Re-check it here so both paths agree.
    if amount_inc_gst > _MAX_AMOUNT_INC_GST:
        raise ExpenseValidationError(
            f"Amount exceeds maximum (${_MAX_AMOUNT_INC_GST:,.0f})"
        )
    # C-6: reject sub-cent precision on both the structured and parser-driven
    # paths. amount_inc_gst is stored in NUMERIC(12,2), so a 3+-decimal value
    # (``$305.999``) would otherwise be silently rounded on insert with no
    # review flag — the stored figure would differ from what the user typed.
    if amount_inc_gst.as_tuple().exponent < -2:
        raise ExpenseValidationError("Amount cannot have more than 2 decimal places")
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
    # CHP-5: reject future-dated expenses. The +1-day tolerance covers
    # phone-clock skew and the NSW/UTC seam — a contributor in Sydney
    # capturing at 11pm local on Mon 12 May submits with their local
    # date; the server's UTC clock is already Tue 13 May. We accept
    # that. Anything beyond +1 day is genuinely wrong.
    future_cutoff = date.today() + timedelta(days=_FUTURE_DATE_TOLERANCE_DAYS)
    if expense_date > future_cutoff:
        raise ExpenseValidationError("Expense date is in the future")


async def validate_job_active_for_reassign(
    db: AsyncSession, job_id: uuid.UUID | None
) -> None:
    """A1: validate the TARGET job of a reassignment exists and is ACTIVE.

    Used by the PATCH path (and, via a ValueError adapter, the
    review-resolve path) when ``job_id`` changes. Mirrors the create-time
    lookup (``JobNotFoundForExpense`` -> 422) and adds the active-only rule
    so a correction can never move spend onto an archived/completed job.
    ``None`` is left for ``validate_save`` to reject as "Job is required".
    """
    if job_id is None:
        return
    job = await db.get(Job, job_id)
    if job is None:
        raise JobNotFoundForExpense(job_id)
    if job.status != JobStatus.active:
        raise ExpenseValidationError(
            "Cannot reassign to an archived or completed job — reopen it first"
        )


# ---------------------------------------------------------------------------
# Audit fields + coercion
# ---------------------------------------------------------------------------


AUDITABLE_FIELDS: tuple[str, ...] = (
    # job_id is admin-only + active-job-only to change (see update_expense /
    # validate_job_active_for_reassign); included here so a reassignment is
    # applied and AUDITED on both the PATCH and review-resolve paths.
    "job_id",
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


def coerce_audit_value(value: Any) -> Any:
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


# ---------------------------------------------------------------------------
# Shared apply / diff primitives (dedupe the two money paths)
#
# The edit path (expenses.update_expense) and the resolve path
# (review_queue.resolve) both: snapshot the auditable pre-image, re-derive
# the GST split when a money field moves, then diff the post-state to build
# the audit ``changed_fields``. These three helpers are the single source
# for that mechanical work; each caller keeps its own audit-gating and
# review_status transition.
# ---------------------------------------------------------------------------


_MONEY_FIELDS = frozenset(
    {"amount_inc_gst", "payment_method", "amount_ex_gst", "gst_amount"}
)


def snapshot_auditable(expense: Expense) -> dict[str, Any]:
    """Pre-image of every auditable field, for a later :func:`diff_auditable`."""
    return {field: getattr(expense, field) for field in AUDITABLE_FIELDS}


def reconcile_money_fields(expense: Expense, patch_set: set[str]) -> None:
    """Re-derive the ex/gst split when any money field moved (audit B-2).

    A lone-component patch (only ``gst_amount``, or only ``amount_ex_gst``)
    re-derives its sibling so the triple stays consistent; a supplied pair
    is validated; cash is forced GST-exclusive. Passing the patched value
    (or ``None`` to re-derive) preserves the legitimate structured-override
    case while making an inconsistent triple impossible. No-op when the
    patch touched no money field.
    """
    if patch_set & _MONEY_FIELDS:
        ex_override = expense.amount_ex_gst if "amount_ex_gst" in patch_set else None
        gst_override = expense.gst_amount if "gst_amount" in patch_set else None
        expense.amount_ex_gst, expense.gst_amount = compute_gst_split(
            expense.amount_inc_gst,
            ex_override,
            gst_override,
            expense.payment_method,
        )


def diff_auditable(
    pre_image: dict[str, Any],
    expense: Expense,
    *,
    skip: frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    """Field-level ``{field: {"old", "new"}}`` diff over the auditable fields.

    Only fields whose value actually changed are included. ``skip`` lets a
    caller own a field's audit entry itself (the resolve path seeds the
    ``review_status`` transition explicitly and skips it here).
    """
    changed: dict[str, dict[str, Any]] = {}
    for field in AUDITABLE_FIELDS:
        if field in skip:
            continue
        old = pre_image[field]
        new = getattr(expense, field)
        if old != new:
            changed[field] = {
                "old": coerce_audit_value(old),
                "new": coerce_audit_value(new),
            }
    return changed
