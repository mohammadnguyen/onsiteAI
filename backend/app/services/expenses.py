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

import base64
import json
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_alias
from app.core.time import app_today
from app.models import (
    Category,
    Expense,
    ExpenseAuditLog,
    ExpenseReviewQueue,
    ExpenseType,
    Job,
    JobStatus,
    PaymentMethod,
    ReceiptStatus,
    ReviewQueueStatus,
    ReviewReasonCode,
    ReviewStatus,
    Supplier,
    User,
    UserRole,
)
from app.models.expense import GstSplitError, reconcile_gst_split
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseUpdate,
    ParseDiagnostics,
    ParsePreview,
)
from app.services.parser import LLMParser, ParseResult, parse
from app.services.parser.tokens import tokenize

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


class InvalidCursor(Exception):
    """Raised when a list-pagination ``cursor`` fails to decode (M2-A).

    Maps to HTTP 400 at the API layer — a malformed cursor is a
    client-side request error, never a server fault. Cursors are
    opaque: clients must only echo ``next_cursor`` back verbatim.
    """

    def __init__(self, detail: str = "Invalid cursor"):
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Internal helpers
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
# CHP-2: actionable error messages for ambiguous / shorthand / no-match
# job-resolution failures on the parser-driven create path.
#
# Per the Capture Hardening Patch behaviour table, an expense is NEVER
# saved with `job_uncertain` (because admin cannot mutate `expenses.job_id`
# after creation — see `_AUDITABLE_FIELDS` and the `ExpenseUpdate` schema).
# Anything less certain than an exact 0.95 parser match returns HTTP 422
# at the contributor's screen with an actionable detail string. The three
# helpers below construct those detail strings.
# ---------------------------------------------------------------------------


# Minimum token length to qualify as a "shorthand suggestion" candidate.
# Avoids noise tokens like "a", "to", "is" producing false suggestions.
_MIN_SUGGESTION_TOKEN_LEN = 3


def _format_job_label(name: str, code: str | None) -> str:
    """Format a single job for display in a 422 detail message.

    ``"Smith Residence (SMITH-01)"`` if a code is present;
    ``"Smith Residence"`` otherwise.
    """
    return f"{name} ({code})" if code else name


def _join_or(items: list[str]) -> str:
    """Join a non-empty list with ``", "``-and-``" or "`` for the last item."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"


async def _format_job_candidates(
    db: AsyncSession, job_ids: list[uuid.UUID] | tuple[uuid.UUID, ...]
) -> str:
    """Build a human-readable candidate list (sorted by name) for an
    ambiguous-job 422 detail message. Returns ``""`` for an empty input
    so callers can fall back to a generic message.
    """
    if not job_ids:
        return ""
    rows = (
        await db.execute(select(Job).where(Job.job_id.in_(list(job_ids))))
    ).scalars().all()
    if not rows:
        return ""
    rows_sorted = sorted(rows, key=lambda j: j.job_name.lower())
    return _join_or([_format_job_label(j.job_name, j.job_code) for j in rows_sorted])


async def _suggest_jobs_from_text(
    db: AsyncSession, raw_text: str | None
) -> list[Job]:
    """Find active jobs whose normalised name STARTS WITH any input token.

    Used by the CHP-2 "Did you mean ...?" suggestion path when the
    parser found no exact match and no ambiguity. Returns the unique
    list of matched jobs (could be 0, 1, or many).

    Token filter rules:
    * Skip currency / numeric-like tokens (jobs aren't named with $ or
      bare digits).
    * Require ``len(normalized) >= _MIN_SUGGESTION_TOKEN_LEN`` so noise
      words don't generate false suggestions.
    """
    if not raw_text:
        return []
    tokens = tokenize(raw_text)
    needles = {
        tok.normalized
        for tok in tokens
        if not tok.is_currency_symbol
        and not tok.is_numeric_like
        and tok.normalized
        and len(tok.normalized) >= _MIN_SUGGESTION_TOKEN_LEN
    }
    if not needles:
        return []

    # Pull all active jobs once, do prefix match in Python — same pattern
    # the parser uses (small N in practice; avoids Postgres-side
    # normalisation issues).
    active_jobs = (
        await db.execute(select(Job).where(Job.status == JobStatus.active))
    ).scalars().all()

    matches: list[Job] = []
    seen: set[uuid.UUID] = set()
    for job in active_jobs:
        name_normal = normalize_alias(job.job_name)
        if not name_normal:
            continue
        for needle in needles:
            if name_normal.startswith(needle):
                if job.job_id not in seen:
                    matches.append(job)
                    seen.add(job.job_id)
                break
    return matches


async def _make_actionable_job_error(
    db: AsyncSession,
    *,
    parse_result: ParseResult,
    raw_text: str | None,
) -> ExpenseValidationError:
    """Build a CHP-2 actionable :class:`ExpenseValidationError` for the
    create-with-raw-text path when the parser couldn't resolve a unique
    job. Picks the most helpful message available:

    1. If the parser returned ambiguous candidates (confidence 0.3,
       multiple unique jobs hit) → "Job is ambiguous: A or B."
    2. Else, fall back to the shorthand-suggestion path:
       a. If exactly one active job's name starts with one of the input
          tokens → "Did you mean Smith Residence (SMITH-01)? ..."
       b. If multiple active jobs do → "Job is ambiguous: A or B ..."
       c. If none → the no-match guidance.
    """
    # Path 1: parser detected true ambiguity at match time.
    if parse_result.ambiguous_job_matches:
        candidates = await _format_job_candidates(db, parse_result.ambiguous_job_matches)
        if candidates:
            return ExpenseValidationError(
                f"Job is ambiguous: {candidates}. "
                "Please retype using the job code or the full job name."
            )

    # Path 2: shorthand-suggestion fallback (parser found nothing).
    suggestions = await _suggest_jobs_from_text(db, raw_text)
    if len(suggestions) == 1:
        job = suggestions[0]
        return ExpenseValidationError(
            f"Did you mean {_format_job_label(job.job_name, job.job_code)}? "
            "Please retype using the job code or the full job name."
        )
    if len(suggestions) > 1:
        # Surface the same shape as path 1 so the contributor's mental
        # model is consistent: "ambiguous → name candidates".
        suggestions_sorted = sorted(suggestions, key=lambda j: j.job_name.lower())
        labelled = _join_or(
            [_format_job_label(j.job_name, j.job_code) for j in suggestions_sorted]
        )
        return ExpenseValidationError(
            f"Job is ambiguous: {labelled}. "
            "Please retype using the job code or the full job name."
        )

    # Path 3: no ambiguity, no shorthand match — the parser truly found
    # nothing job-shaped in the input.
    return ExpenseValidationError(
        "Couldn't identify a job — please mention a job code "
        "(e.g. SMITH-01) or the full job name in your text."
    )


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
    cutoff = app_today() - timedelta(days=365 * _MAX_PAST_YEARS)
    if expense_date < cutoff:
        raise ExpenseValidationError("Expense date is more than 5 years in the past")
    # CHP-5: reject future-dated expenses. The +1-day tolerance covers
    # phone-clock skew and the NSW/UTC seam — a contributor in Sydney
    # capturing at 11pm local on Mon 12 May submits with their local
    # date; the server's UTC clock is already Tue 13 May. We accept
    # that. Anything beyond +1 day is genuinely wrong.
    future_cutoff = app_today() + timedelta(days=_FUTURE_DATE_TOLERANCE_DAYS)
    if expense_date > future_cutoff:
        raise ExpenseValidationError("Expense date is in the future")


async def _validate_job_active_for_reassign(
    db: AsyncSession, job_id: uuid.UUID | None
) -> None:
    """A1: validate the TARGET job of a reassignment exists and is ACTIVE.

    Used by the PATCH path (and, via a ValueError adapter, the
    review-resolve path) when ``job_id`` changes. Mirrors the create-time
    lookup (``JobNotFoundForExpense`` -> 422) and adds the active-only rule
    so a correction can never move spend onto an archived/completed job.
    ``None`` is left for ``_validate_save`` to reject as "Job is required".
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
    expense_date = payload.expense_date or app_today()
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

    # CHP-2: when the parser ran and couldn't resolve a job (and the
    # caller didn't supply one structurally), produce an actionable
    # 422 detail before falling through to the generic "Job is required"
    # in `_validate_save`. This is the only place we have the
    # parse_result + raw_text in scope to build the suggestion.
    if (
        parse_result is not None
        and merged["job_id"] is None
        and "job_id" not in caller_set
    ):
        raise await _make_actionable_job_error(
            db,
            parse_result=parse_result,
            raw_text=payload.raw_input_text,
        )

    # Finalise validation + FK pre-checks.
    _validate_save(
        job_id=merged["job_id"],
        supplier_id=merged["supplier_id"],
        amount_inc_gst=merged["amount_inc_gst"],
        expense_date=expense_date,
        expense_type=merged["expense_type"],
        description=merged["description"],
    )
    # Confirm the job id refers to a real, ACTIVE row. The parser only
    # resolves active jobs, but a structured/override ``job_id`` could
    # point at an archived/completed job; the PATCH path already forbids
    # this, so mirror it here (audit R32) — spend can't be booked to a
    # non-active job on create either.
    job = await db.get(Job, merged["job_id"])
    if job is None:
        raise JobNotFoundForExpense(merged["job_id"])
    if job.status != JobStatus.active:
        raise ExpenseValidationError(
            "Cannot add an expense to an archived or completed job — reopen it first"
        )
    await _validate_fk_refs(
        db,
        job_id=merged["job_id"],
        supplier_id=merged["supplier_id"],
        category_id=merged["category_id"],
    )

    # Compute GST split up front (the model listener would do this at
    # flush time; doing it here keeps the wire response immediately
    # consistent). Cash payments are GST-exclusive — see
    # :func:`app.models.expense.compute_gst_split` for the business
    # rule documentation.
    amount_ex, gst = _compute_gst_split(
        merged["amount_inc_gst"],
        merged["amount_ex_gst"],
        merged["gst_amount"],
        merged["payment_method"],
    )

    # Determine review routing. Start from the parser's gating reasons
    # (empty for a structured-only submission).
    review_reasons: list[ReviewReasonCode] = (
        list(parse_result.review_reasons) if parse_result is not None else []
    )

    # Audit R1: a contributor's amount must be established by the parser's
    # confidence pipeline. A structured-only submission (no parser ran) or
    # a caller override of any money field would otherwise persist as
    # ``reviewed`` with no queue row — a silent review bypass. Force such
    # amounts to review. Admins retain the trusted structured path.
    if entered_by.role != UserRole.admin:
        money_overridden = bool(
            {"amount_inc_gst", "amount_ex_gst", "gst_amount"} & caller_set
        )
        if (parse_result is None or money_overridden) and (
            ReviewReasonCode.amount_uncertain not in review_reasons
        ):
            review_reasons.append(ReviewReasonCode.amount_uncertain)

    review_status = ReviewStatus.pending if review_reasons else ReviewStatus.reviewed
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

    # Enqueue a review-queue row whenever there are gating reasons — from
    # the parser or from the contributor money-review rule above.
    if review_reasons:
        queue_row = ExpenseReviewQueue(
            review_id=uuid.uuid4(),
            expense_id=expense.expense_id,
            review_reasons=review_reasons,
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
    when = expense_date or app_today()
    result = await parse(
        raw_text=raw_text,
        db=db,
        entered_by=entered_by,
        expense_date=when,
        expense_type=expense_type,
        llm_parser=llm_parser,
    )

    # Build a best-guess ExpenseCreate draft from the parser output.
    # Not persisted — just a structured "here's what we'd write". Use
    # ``model_construct`` so an out-of-range parser amount (``$0``,
    # ``$20M``) shown for review does not trip ExpenseCreate's field
    # validators and turn a preview into a 500 (audit R17). Create-time
    # validation still rejects such values with a clean 422.
    p = result.partial
    draft = ExpenseCreate.model_construct(
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


# M2-A: keyset-pagination cursor. The wire format is OPAQUE —
# base64url-encoded JSON of the last row's (expense_date, created_at,
# expense_id). Clients must treat ``next_cursor`` as a token to echo
# back verbatim, never parse it. The triple mirrors the fixed ORDER BY
# exactly (date DESC, created_at DESC, id DESC) so a row-value
# comparison resumes the scan with no duplicates and no skips —
# including full ties: ``created_at`` is the transaction timestamp
# (server_default now()), so multi-row captures share it and
# ``expense_id`` breaks the tie.


def _encode_cursor(row: Expense) -> str:
    payload = json.dumps(
        {
            "d": row.expense_date.isoformat(),
            "c": row.created_at.isoformat(),
            "i": row.expense_id.hex,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[date, datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return (
            date.fromisoformat(payload["d"]),
            datetime.fromisoformat(payload["c"]),
            uuid.UUID(hex=payload["i"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # binascii.Error / JSONDecodeError / UnicodeError are ValueError
        # subclasses; non-dict payloads raise TypeError; missing keys
        # raise KeyError. Anything undecodable is the client's problem.
        raise InvalidCursor() from exc


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
    supplier_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[list[Expense], str | None]:
    """List expenses (contributors always restricted to their own rows).

    M2-A: real keyset pagination. The order is fixed at
    ``expense_date DESC, created_at DESC, expense_id DESC`` — the id
    tiebreak makes the order total, which keyset resumption requires.
    ``cursor`` is the opaque ``next_cursor`` from the previous page;
    passing it back resumes the scan strictly after that row under the
    same filters. ``next_cursor`` is ``None`` on the last page. An
    undecodable cursor raises :class:`InvalidCursor` (HTTP 400 at the
    API layer), never a 500.
    """
    stmt = select(Expense).order_by(
        Expense.expense_date.desc(),
        Expense.created_at.desc(),
        Expense.expense_id.desc(),
    )

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
    if supplier_id is not None:
        stmt = stmt.where(Expense.supplier_id == supplier_id)
    if category_id is not None:
        stmt = stmt.where(Expense.category_id == category_id)

    if cursor is not None:
        last_date, last_created, last_id = _decode_cursor(cursor)
        # Row-value comparison: under an all-DESC order, the "next"
        # rows are exactly those whose (date, created_at, id) tuple is
        # strictly smaller than the cursor row's. Postgres evaluates
        # this natively; filters above still apply.
        stmt = stmt.where(
            tuple_(Expense.expense_date, Expense.created_at, Expense.expense_id)
            < (last_date, last_created, last_id)
        )

    stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])

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


async def get_expense_with_reasons(
    db: AsyncSession,
    *,
    current_user: User,
    expense_id: uuid.UUID,
) -> tuple[Expense, list[ReviewReasonCode], uuid.UUID | None]:
    """Fetch one expense + queue context for the detail endpoint.

    Returns a three-tuple:
      * ``expense`` — the :class:`Expense` row (RBAC-checked via
        :func:`get_expense` — contributor ownership enforced).
      * ``reasons`` — the current queue row's ``review_reasons`` list
        (any status). NOT a historical audit trail. ``[]`` when no
        queue row exists.
      * ``pending_review_queue_id`` — the ``review_id`` of the queue
        row IFF its status is :attr:`ReviewQueueStatus.open` (i.e.
        currently actionable). ``None`` for resolved / rejected /
        absent queue rows. Mobile gates Approve / Reject button
        visibility on this — stale resolved/rejected queue rows must
        NOT surface as actionable.

    Since the one-open-row constraint is now a partial unique index
    (audit D-6/T-2), an expense may have at most one OPEN row but could
    accumulate closed (resolved/rejected) history rows in future flows.
    The query therefore orders the OPEN row first (then most-recent) and
    takes one, so the actionable row is preferred over stale history.
    """
    expense = await get_expense(db, current_user=current_user, expense_id=expense_id)

    queue_stmt = (
        select(
            ExpenseReviewQueue.review_id,
            ExpenseReviewQueue.status,
            ExpenseReviewQueue.review_reasons,
        )
        .where(ExpenseReviewQueue.expense_id == expense_id)
        .order_by(
            (ExpenseReviewQueue.status == ReviewQueueStatus.open).desc(),
            ExpenseReviewQueue.opened_at.desc(),
        )
    )
    row = (await db.execute(queue_stmt)).first()
    if row is None:
        return expense, [], None
    review_id, queue_status, reasons_array = row
    reasons: list[ReviewReasonCode] = list(reasons_array)
    pending_review_queue_id = (
        review_id if queue_status == ReviewQueueStatus.open else None
    )
    return expense, reasons, pending_review_queue_id


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


# Columns an audit row diff may cover. ``review_status`` transitions
# always write audit rows; the other fields only write audit rows when
# an admin touches a reviewed row.
_AUDITABLE_FIELDS: tuple[str, ...] = (
    # job_id is admin-only + active-job-only to change (see update_expense /
    # _validate_job_active_for_reassign); included here so a reassignment is
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


# A1b (review-queue lifecycle): which MONEY-integrity reason each
# explicitly-patched field resolves. A human setting the field is a
# trusted, deterministic correction — the parser is NOT re-run.
# ``duplicate_suspected`` is deliberately absent: a suspected duplicate
# is only cleared by an explicit resolve/reject, never by a field edit.
def _money_reasons_cleared_by_patch(patch_set: set[str]) -> set[ReviewReasonCode]:
    cleared: set[ReviewReasonCode] = set()
    if "amount_inc_gst" in patch_set:
        # A human-entered amount is trusted; Phase 2 is AUD-only, so a
        # corrected amount also resolves an unsupported-currency flag.
        cleared.add(ReviewReasonCode.amount_uncertain)
        cleared.add(ReviewReasonCode.unsupported_currency)
    if "job_id" in patch_set:
        cleared.add(ReviewReasonCode.job_uncertain)
    return cleared


async def _reconcile_open_review_after_edit(
    db: AsyncSession,
    *,
    expense: Expense,
    patch_set: set[str],
    actor: User,
    reason: str | None,
) -> None:
    """Close or shrink an open review-queue row after a trusted admin edit.

    A1b dangling-row fix: when an admin corrects the field(s) behind an
    open row's money reasons, drop those reasons. If none remain, close
    the row (``resolved``) and mark the expense ``reviewed``; otherwise
    keep it open with the still-unresolved reasons. Every change writes an
    audit row (review_reasons before/after + any review_status transition).
    No-ops when there is no open row or the edit touched no row reason.
    Callers gate this to admin edits of still-pending expenses (a
    contributor is not a reviewer; reviewed expenses are never reopened).
    """
    cleared = _money_reasons_cleared_by_patch(patch_set)
    if not cleared:
        return
    queue_row = await _get_open_queue_row(db, expense.expense_id)
    if queue_row is None:
        return
    before = list(queue_row.review_reasons)
    remaining = [r for r in before if r not in cleared]
    if remaining == before:
        return

    changed_fields: dict[str, dict[str, Any]] = {
        "review_reasons": {
            "old": [_coerce_audit_value(r) for r in before],
            "new": [_coerce_audit_value(r) for r in remaining],
        }
    }
    if remaining:
        # Money reasons remain → keep the row open, shrink its reasons.
        queue_row.review_reasons = remaining
    else:
        # Last money reason resolved → close the row + mark reviewed. The
        # closed row keeps its original reasons array for history (the
        # cardinality>0 CHECK is never violated — we don't empty it).
        from datetime import UTC, datetime

        queue_row.status = ReviewQueueStatus.resolved
        queue_row.resolved_by_user_id = actor.user_id
        queue_row.resolved_at = datetime.now(UTC)
        changed_fields["review_status"] = {
            "old": _coerce_audit_value(expense.review_status),
            "new": _coerce_audit_value(ReviewStatus.reviewed),
        }
        expense.review_status = ReviewStatus.reviewed
    await db.flush()

    audit = ExpenseAuditLog(
        audit_id=uuid.uuid4(),
        expense_id=expense.expense_id,
        edited_by_user_id=actor.user_id,
        changed_fields=changed_fields,
        reason=reason,
    )
    db.add(audit)
    await db.flush()


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

    # A1 (Option A): job reassignment rules. The generic apply loop below
    # writes (and audits) job_id via _AUDITABLE_FIELDS; these guards run
    # FIRST (before any mutation) for clean 403/422s.
    if "job_id" in patch_set:
        # An expense must ALWAYS have a job — never allow clearing it. This
        # is a deliberate contract: explicit null is rejected here (not left
        # to _validate_save), so the 422 is unambiguous and no mutation runs.
        if patch.job_id is None:
            raise ExpenseValidationError(
                "An expense must always have a job; job_id cannot be cleared"
            )
        # Reassigning to a DIFFERENT job is admin-only + active-job-only. A
        # no-op (same job_id) is neither blocked nor active-validated.
        if patch.job_id != expense.job_id:
            if not is_admin:
                raise EditForbidden(
                    "Only admins can reassign an expense to another job"
                )
            await _validate_job_active_for_reassign(db, patch.job_id)

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

    # Re-reconcile the GST split whenever ANY money field moves (audit B-2).
    # A lone-component patch (only gst_amount, or only amount_ex_gst) must
    # re-derive its sibling so the triple stays consistent; a supplied pair
    # is validated; cash is forced GST-exclusive. Passing the patched value
    # (or None to re-derive) preserves the legitimate structured-override
    # case while making an inconsistent triple impossible.
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

    # A1b lifecycle: a trusted ADMIN edit to a still-pending expense that
    # resolves the money reason(s) behind its open review-queue row closes
    # (or shrinks) that row, so the active queue never carries reasons the
    # correction already fixed — the dangling-row fix. Contributor edits
    # never auto-close (a contributor is not a reviewer); reviewed expenses
    # are never auto-reopened (D2). Skipped when the admin explicitly set
    # review_status (they have taken manual control of the transition).
    if (
        is_admin
        and pre_status == ReviewStatus.pending
        and "review_status" not in patch_set
    ):
        await _reconcile_open_review_after_edit(
            db,
            expense=expense,
            patch_set=patch_set,
            actor=current_user,
            reason=patch.reason,
        )

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
