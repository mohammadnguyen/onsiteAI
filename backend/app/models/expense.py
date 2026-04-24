"""Expense model for Phase 2.

An :class:`Expense` is a single money-movement row booked against a
:class:`app.models.job.Job`. Phase 2 supports three kinds of expense
(see :class:`ExpenseType`): ordinary supplier receipts, labour entries
(where ``supplier_id`` is null), and manual adjustments.

GST split convention
--------------------
V1 operates under the Australian 10% GST regime where the inclusive
price is the "source of truth" for most payments. When the caller
supplies only ``amount_inc_gst``, the ``_compute_gst_split`` event
listener derives the ex-GST / GST components using one of two rules
driven by ``payment_method``:

* ``cash`` — treated as **GST-exclusive**. Small cash purchases in
  Australian residential construction typically lack a tax invoice,
  so we can't claim the GST input credit. The captured amount IS the
  ex-GST amount and the GST component is zero::

      amount_ex_gst = amount_inc_gst
      gst_amount    = Decimal("0.00")

* any other payment method (``transfer`` / ``unknown``) — standard
  1/11 split::

      amount_ex_gst = round(amount_inc_gst / Decimal("1.1"), 2)
      gst_amount    = amount_inc_gst - amount_ex_gst

Structured entry (bookkeeping-style) may pass all three amounts
explicitly — if the listener sees a value already set on
``amount_ex_gst`` or ``gst_amount`` it leaves that value alone but
still fills any unset sibling from the others. The rule is: only
compute into a column that is currently ``None``.

Enums
-----
Four new Postgres ENUM types are created alongside this model:

* ``expense_type``   — ``supplier_expense`` | ``labour`` | ``adjustment``
* ``payment_method`` — ``cash`` | ``transfer`` | ``unknown``
* ``receipt_status`` — ``no_receipt`` | ``expected_later`` (only these
  two in Phase 2; a third "receipt on file" value is introduced in
  Phase 5)
* ``review_status``  — ``pending`` | ``reviewed`` | ``rejected``

``review_queue_status`` / ``review_reason_code`` belong to Task T-C and
are intentionally not introduced here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Boolean, Date, Index, Numeric, Text
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy import ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.job import Job
    from app.models.supplier import Supplier
    from app.models.user import User


class ExpenseType(str, enum.Enum):
    """What kind of money-movement this expense represents."""

    supplier_expense = "supplier_expense"
    labour = "labour"
    adjustment = "adjustment"


class PaymentMethod(str, enum.Enum):
    """How the expense was settled.

    ``unknown`` is the default because the Phase 2 parser often cannot
    determine the payment method from free text.
    """

    cash = "cash"
    transfer = "transfer"
    unknown = "unknown"


class ReceiptStatus(str, enum.Enum):
    """Lifecycle of the physical / digital receipt for this expense.

    Phase 2 has only two values — ``no_receipt`` (default, nothing on
    file) and ``expected_later`` (admin flagged that a receipt will be
    supplied later). A third "attached" value is introduced in Phase 5
    when the receipt upload flow goes live; it must NOT be added here.
    """

    no_receipt = "no_receipt"
    expected_later = "expected_later"


class ReviewStatus(str, enum.Enum):
    """Admin review verdict on an expense row."""

    pending = "pending"
    reviewed = "reviewed"
    rejected = "rejected"


class Expense(Base, TimestampMixin):
    """A money-movement row booked against a :class:`Job`."""

    __tablename__ = "expenses"

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # V1 does not delete jobs, so no ondelete cascade is needed here.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False
    )
    # Labour / adjustment entries may have no supplier.
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=True
    )
    entered_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    expense_type: Mapped[ExpenseType] = mapped_column(
        SqlaEnum(
            ExpenseType,
            name="expense_type",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=ExpenseType.supplier_expense,
        server_default=ExpenseType.supplier_expense.value,
    )
    # The raw text the parser saw (for diagnostics / reprocessing).
    raw_input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount_inc_gst: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Auto-computed from ``amount_inc_gst`` when not set by the caller.
    # See the module docstring + ``_compute_gst_split`` below.
    amount_ex_gst: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        SqlaEnum(
            PaymentMethod,
            name="payment_method",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=PaymentMethod.unknown,
        server_default=PaymentMethod.unknown.value,
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.category_id"), nullable=True
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        SqlaEnum(
            ReviewStatus,
            name="review_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=ReviewStatus.pending,
        server_default=ReviewStatus.pending.value,
    )
    receipt_status: Mapped[ReceiptStatus] = mapped_column(
        SqlaEnum(
            ReceiptStatus,
            name="receipt_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=ReceiptStatus.no_receipt,
        server_default=ReceiptStatus.no_receipt.value,
    )
    # Diagnostic display value from the parser (0.00 .. 1.00).
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True
    )
    duplicate_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Self-referential FK: ``duplicate_of`` points at the "original"
    # expense when the parser judged this row to be a duplicate. No
    # cascade — deleting the original must not silently delete flagged
    # duplicates (they're still useful evidence in review).
    duplicate_of_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expenses.expense_id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships. ``lazy="joined"`` on the small, single-row FKs keeps
    # the common "load one expense with its context" path to one SQL
    # statement; the list-style queries in services will use explicit
    # ``selectinload`` to avoid cartesian blow-ups.
    job: Mapped["Job"] = relationship(back_populates=None)
    supplier: Mapped["Supplier | None"] = relationship(lazy="joined")
    entered_by: Mapped["User"] = relationship(
        lazy="joined", foreign_keys=[entered_by_user_id]
    )
    category: Mapped["Category | None"] = relationship(lazy="joined")
    duplicate_of: Mapped["Expense | None"] = relationship(
        remote_side=[expense_id], foreign_keys=[duplicate_of_expense_id]
    )

    __table_args__ = (
        # Common access pattern: list expenses for a job, newest first.
        Index(
            "ix_expenses_job_id_expense_date",
            "job_id",
            "expense_date",
            postgresql_using="btree",
        ),
        Index("ix_expenses_entered_by_user_id", "entered_by_user_id"),
        Index("ix_expenses_review_status", "review_status"),
        Index("ix_expenses_category_id", "category_id"),
        Index("ix_expenses_supplier_id", "supplier_id"),
    )


# Auto-compute the ex-GST / GST split when the caller supplies only the
# inclusive total. The rule is: only fill in a column whose current
# value is ``None`` — structured entry (bookkeeping-style) is free to
# pass all three amounts explicitly and the listener will leave them
# alone. See the module docstring for the full convention.
_GST_DIVISOR = Decimal("1.1")


def compute_gst_split(
    amount_inc_gst: Decimal,
    payment_method: "PaymentMethod | str | None",
) -> tuple[Decimal, Decimal]:
    """Return ``(amount_ex_gst, gst_amount)`` from an inclusive total.

    Business rule is driven by the payment method:

    * ``cash`` → GST-exclusive. The captured amount becomes
      ``amount_ex_gst`` verbatim and ``gst_amount`` is ``0.00``. Small
      cash builder purchases typically have no tax invoice, so the
      input credit can't be claimed and carrying a phantom GST figure
      on the row would misrepresent the books.

    * anything else (``transfer`` / ``unknown`` / ``None``) → standard
      Australian 1/11 split.

    This helper is the single source of truth for the rule; both the
    ``before_insert`` / ``before_update`` listener below and the
    service-layer eager compute in :mod:`app.services.expenses` call
    into it so the split can't drift across call sites.
    """
    pm = (
        payment_method.value
        if isinstance(payment_method, PaymentMethod)
        else (payment_method or "")
    )
    if pm == PaymentMethod.cash.value:
        ex = amount_inc_gst.quantize(Decimal("0.01"))
        return ex, Decimal("0.00")
    ex = (amount_inc_gst / _GST_DIVISOR).quantize(Decimal("0.01"))
    return ex, amount_inc_gst - ex


def _compute_gst_split(mapper, connection, target: Expense) -> None:
    if target.amount_inc_gst is None:
        return
    inc = target.amount_inc_gst
    if target.amount_ex_gst is None and target.gst_amount is None:
        ex, gst = compute_gst_split(inc, target.payment_method)
        target.amount_ex_gst = ex
        target.gst_amount = gst
    elif target.amount_ex_gst is None:
        target.amount_ex_gst = inc - target.gst_amount
    elif target.gst_amount is None:
        target.gst_amount = inc - target.amount_ex_gst


event.listen(Expense, "before_insert", _compute_gst_split, propagate=True)
event.listen(Expense, "before_update", _compute_gst_split, propagate=True)
