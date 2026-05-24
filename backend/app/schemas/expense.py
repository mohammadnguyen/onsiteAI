"""Public-facing expense schemas for Phase 2 Task T-M.

Shapes for ``/expenses`` HTTP routes. Three inbound bodies
(:class:`ExpenseCreate`, :class:`ExpenseUpdate` and the inline
``/expenses/parse`` body), three main outbound shapes
(:class:`ExpensePublic`, :class:`ExpenseDetailPublic`,
:class:`ExpenseListResponse`), plus the create and parse-preview
response envelopes that carry :class:`ParseDiagnostics` for
``raw_input_text``-driven submissions.

Design notes
------------
* :class:`ExpenseCreate` tolerates **either** ``raw_input_text`` alone
  or a fully-structured body. The service layer merges parser output
  with any explicitly-supplied structured fields (structured wins).
* :class:`ExpenseUpdate` carries an optional ``reason`` that is NOT
  persisted to the expense itself — it flows into the audit log when
  an admin edits a ``reviewed`` row.
* :class:`ParseDiagnostics` is a subset of the parser's
  :class:`~app.services.parser.ParseResult`: the confidences, review
  reason codes, ambiguity tuples, matched-via labels, the candidate
  supplier name, a duplicate-of pointer, and the per-field source
  map. Clients use it to explain to admins why a row landed in the
  review queue.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.models import (
    ExpenseType,
    PaymentMethod,
    ReceiptStatus,
    ReviewReasonCode,
    ReviewStatus,
)

# Note: ReviewReasonCode is imported above and used by both
# ParseDiagnostics.review_reasons and the new
# ExpenseDetailPublic.review_reasons field added for the mobile detail
# screen.
from app.schemas.category import CategoryPublic
from app.schemas.supplier import SupplierPublic
from app.services.parser.dates import parse_loose_date


def _normalize_loose_expense_date(v: object) -> date | None:
    """Pydantic ``mode='before'`` hook for the ``expense_date`` field.

    Lets the field accept loose user-typed formats (``22/05``,
    ``22-05``, ``22.05``, ``22/05/26``, etc.) in addition to canonical
    ISO ``YYYY-MM-DD``. ``None`` and pre-parsed :class:`~datetime.date`
    instances pass through unchanged. Strings route through
    :func:`app.services.parser.dates.parse_loose_date`. Anything else
    raises so Pydantic surfaces a clean 422.

    Backend-owned per the P3 design: mobile/admin SHOULD send ISO,
    but the backend is the single source of truth for date parsing
    so the system stays correct for non-client callers (curl, future
    bulk import, parser-extracted dates, etc.).
    """
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, str):
        return parse_loose_date(v)
    raise ValueError(
        f"expense_date must be a date or string, got {type(v).__name__}"
    )


ExpenseDateField = Annotated[
    date | None, BeforeValidator(_normalize_loose_expense_date)
]


class ExpenseCreate(BaseModel):
    """Body of ``POST /expenses`` and the draft carrier in ``ParsePreview``.

    Two submission modes are supported:

    * **raw_input_text mode** — the caller passes only
      ``raw_input_text`` (plus optional overrides) and the service
      layer runs the parser first, merging any explicitly-set
      structured fields on top of the parser's draft.
    * **structured mode** — the caller supplies all the required
      structured fields (``job_id``, ``amount_inc_gst``, etc.) and
      ``raw_input_text`` is omitted; the parser is NOT invoked.

    Validation of required fields (amount, job, supplier-or-description
    for supplier expenses, date sanity) is performed in the service.
    """

    raw_input_text: str | None = Field(default=None, max_length=2000)
    job_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    expense_type: ExpenseType = ExpenseType.supplier_expense
    amount_inc_gst: Decimal | None = Field(default=None, gt=0, le=Decimal("10000000"))
    amount_ex_gst: Decimal | None = Field(default=None, ge=0)
    gst_amount: Decimal | None = Field(default=None, ge=0)
    payment_method: PaymentMethod = PaymentMethod.unknown
    # Defaults to today in the service when unset.
    expense_date: ExpenseDateField = None
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    receipt_status: ReceiptStatus = ReceiptStatus.no_receipt


class ExpenseUpdate(BaseModel):
    """Body of ``PATCH /expenses/{id}``. Every field optional.

    ``reason`` is an out-of-band audit note for admin edits on reviewed
    rows — it is NOT written back as a column on the expense.
    """

    supplier_id: uuid.UUID | None = None
    expense_type: ExpenseType | None = None
    amount_inc_gst: Decimal | None = Field(default=None, gt=0, le=Decimal("10000000"))
    amount_ex_gst: Decimal | None = Field(default=None, ge=0)
    gst_amount: Decimal | None = Field(default=None, ge=0)
    payment_method: PaymentMethod | None = None
    expense_date: ExpenseDateField = None
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    receipt_status: ReceiptStatus | None = None
    review_status: ReviewStatus | None = None
    reason: str | None = Field(default=None, max_length=500)


class ParsePreviewRequest(BaseModel):
    """Body of ``POST /expenses/parse``.

    A minimal request shape that does not force the caller to supply
    every :class:`ExpenseCreate` field. Only ``raw_input_text`` is
    required; ``expense_date`` defaults to today in the service and
    ``expense_type`` defaults to :attr:`ExpenseType.supplier_expense`.
    """

    raw_input_text: str = Field(min_length=1, max_length=2000)
    expense_date: ExpenseDateField = None
    expense_type: ExpenseType = ExpenseType.supplier_expense


class ParseDiagnostics(BaseModel):
    """Subset of the parser's ``ParseResult`` exposed to API clients.

    Includes per-stage confidences, the derived review reason codes,
    ambiguity tuples, the ``matched_via`` labels, the candidate
    supplier proposal, the duplicate-of pointer, and the per-field
    source map (``{"amount": "rules", "job": "rules", ...}``) so the
    UI can render "which fields did rules populate vs LLM".
    """

    amount_conf: float
    job_conf: float
    supplier_conf: float
    category_conf: float
    unsupported_currency: bool
    review_reasons: list[ReviewReasonCode]
    ambiguous_job_matches: list[uuid.UUID]
    ambiguous_supplier_matches: list[uuid.UUID]
    matched_job_via: str | None
    matched_supplier_via: str | None
    candidate_supplier_name: str | None
    duplicate_of_expense_id: uuid.UUID | None
    source_per_field: dict[str, str]


class ExpensePublic(BaseModel):
    """Compact expense wire shape for list + post-create + post-patch."""

    model_config = ConfigDict(from_attributes=True)

    expense_id: uuid.UUID
    job_id: uuid.UUID
    supplier_id: uuid.UUID | None
    entered_by_user_id: uuid.UUID
    expense_type: ExpenseType
    raw_input_text: str | None
    description: str | None
    amount_inc_gst: Decimal
    amount_ex_gst: Decimal
    gst_amount: Decimal
    payment_method: PaymentMethod
    expense_date: date
    category_id: uuid.UUID | None
    review_status: ReviewStatus
    receipt_status: ReceiptStatus
    confidence_score: Decimal | None
    duplicate_flag: bool
    duplicate_of_expense_id: uuid.UUID | None
    notes: str | None


class ExpenseCreateResponse(BaseModel):
    """Body of ``POST /expenses`` 201 response.

    Carries the persisted :class:`ExpensePublic` plus an optional
    :class:`ParseDiagnostics` block — populated iff
    ``raw_input_text`` drove the create. Structured-only submissions
    return ``parse=None``.
    """

    expense: ExpensePublic
    parse: ParseDiagnostics | None = None


class ExpenseDetailPublic(ExpensePublic):
    """Body of ``GET /expenses/{id}`` — adds nested supplier + category.

    ``review_reasons`` reflects the *current* row in
    ``expense_review_queue`` for this expense (any status: ``open``,
    ``resolved`` or ``rejected``) and is ``[]`` when no queue row
    exists. It is NOT a historical audit trail — for that, admins use
    ``GET /expenses/{id}/audit``.

    ``pending_review_queue_id`` is the ``review_id`` of the
    *currently actionable* ``expense_review_queue`` row — i.e. one
    whose ``status == open``. It is ``None`` when there is no
    actionable queue row, which includes:
      * no queue row ever existed,
      * the queue row was resolved (expense is reviewed), OR
      * the queue row was rejected.
    Mobile clients gate Approve / Reject buttons on this field's
    presence — never on ``review_status`` alone, because a
    historical resolved/rejected row would otherwise leak as a
    callable queue action. Stale queue rows MUST NOT surface here.
    """

    supplier: SupplierPublic | None
    category: CategoryPublic | None
    review_reasons: list[ReviewReasonCode] = Field(default_factory=list)
    pending_review_queue_id: uuid.UUID | None = None


class ExpenseListResponse(BaseModel):
    """Body of ``GET /expenses``. Supports cursor-style pagination."""

    items: list[ExpensePublic]
    next_cursor: str | None = None


class ParsePreview(BaseModel):
    """Body of ``POST /expenses/parse`` 200 response.

    Does NOT persist anything: returns the parser's best-guess
    :class:`ExpenseCreate` draft together with the diagnostics block
    so the UI can show confidences + review reasons before the user
    commits with ``POST /expenses``.
    """

    draft: ExpenseCreate
    diagnostics: ParseDiagnostics


class AuditRow(BaseModel):
    """Wire shape of a single :class:`ExpenseAuditLog` row."""

    model_config = ConfigDict(from_attributes=True)

    audit_id: uuid.UUID
    expense_id: uuid.UUID
    edited_by_user_id: uuid.UUID
    edited_at: datetime
    changed_fields: dict
    reason: str | None
