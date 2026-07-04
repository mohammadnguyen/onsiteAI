"""Review queue + audit log models for Phase 2 Task T-C.

Two tables:

* :class:`ExpenseReviewQueue` — an open/resolved/rejected queue of
  expense rows that need admin eyes. At most one OPEN row per expense
  (enforced by a partial unique index on ``expense_id`` WHERE
  ``status='open'``); resolved/rejected history rows may accumulate. The
  reasons the row landed in the queue are modeled as a Postgres
  ``review_reason_code[]`` array with a CHECK that the array is
  non-empty.
* :class:`ExpenseAuditLog` — an append-only log of post-creation edits
  to an expense row. ``changed_fields`` is a JSONB blob; the exact
  shape is left to the service layer (Task T-E) but convention is
  ``{"field": {"old": ..., "new": ...}}``.

Neither table uses :class:`TimestampMixin`: their lifecycles are
captured explicitly by their own timestamp columns (``opened_at`` /
``resolved_at`` on the queue, ``edited_at`` on the audit log).

Enum ``ReviewReasonCode`` value order is canonical — the UI renders
reason chips in this order, so do not reorder.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import UUID, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.expense import Expense
    from app.models.user import User


class ReviewQueueStatus(str, enum.Enum):
    """Lifecycle of a queued review."""

    open = "open"
    resolved = "resolved"
    rejected = "rejected"


class ReviewReasonCode(str, enum.Enum):
    """Why an expense landed in the review queue.

    The declaration order is canonical — the UI renders reason chips
    in exactly this order. Do not reorder without a plan-level change.
    """

    job_uncertain = "job_uncertain"
    supplier_uncertain = "supplier_uncertain"
    category_uncertain = "category_uncertain"
    amount_uncertain = "amount_uncertain"
    duplicate_suspected = "duplicate_suspected"
    unsupported_currency = "unsupported_currency"


class ExpenseReviewQueue(Base):
    """An open/resolved/rejected review entry for a single expense."""

    __tablename__ = "expense_review_queue"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.expense_id", ondelete="CASCADE"),
        nullable=False,
    )
    review_reasons: Mapped[list[ReviewReasonCode]] = mapped_column(
        ARRAY(
            SqlaEnum(
                ReviewReasonCode,
                name="review_reason_code",
                native_enum=True,
                create_type=True,
            )
        ),
        nullable=False,
    )
    status: Mapped[ReviewQueueStatus] = mapped_column(
        SqlaEnum(
            ReviewQueueStatus,
            name="review_queue_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=ReviewQueueStatus.open,
        server_default=ReviewQueueStatus.open.value,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # One-way navigation is all Phase 2 tests need. Keeping these one-way
    # avoids having to add back-populates on Expense / User.
    expense: Mapped["Expense"] = relationship()
    resolved_by: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (
        # Audit D-6 / T-2: one OPEN row per expense (matching ADR 0001's
        # stated "one open row per expense"), NOT one row for all time. A
        # plain UNIQUE(expense_id) made the review lifecycle a one-way dead
        # end — once any row existed (even resolved/rejected), an expense
        # could never be re-queued, so a future "send back to review" or a
        # re-flag on edit would hit an IntegrityError and 500. A partial
        # unique index preserves history (closed rows stay) while still
        # forbidding two simultaneously-open rows.
        Index(
            "uq_expense_review_queue_one_open",
            "expense_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        CheckConstraint(
            "cardinality(review_reasons) > 0",
            name="ck_expense_review_queue_reasons_non_empty",
        ),
    )


class ExpenseAuditLog(Base):
    """Append-only log of edits applied to an expense after creation."""

    __tablename__ = "expense_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.expense_id", ondelete="CASCADE"),
        nullable=False,
    )
    edited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    expense: Mapped["Expense"] = relationship()
    edited_by: Mapped["User"] = relationship(lazy="joined")
