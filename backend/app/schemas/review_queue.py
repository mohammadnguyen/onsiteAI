"""Public-facing review-queue schemas for Phase 2 Task T-N.

Shapes for ``/review-queue`` HTTP routes. Two outbound models
(:class:`ReviewQueuePublic`, :class:`ReviewQueueDetail`) and two inbound
bodies (:class:`ResolveRequest`, :class:`RejectRequest`).

Design notes
------------
* :class:`ReviewQueueDetail` bundles the queue row, the full expense
  being reviewed, and — when the queue row's expense was flagged as a
  duplicate of a prior entry — the earlier expense that produced the
  flag. The parser's ambiguous-match tuples are not persisted on the
  queue row, so they're not surfaced here either.
* :class:`ResolveRequest` re-uses :class:`ExpenseUpdate` as its optional
  ``expense_patch`` field so admins can approve-and-edit in a single
  request. Any change it induces is folded into the single audit row
  the resolve operation writes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ReviewQueueStatus, ReviewReasonCode
from app.schemas.expense import ExpenseDetailPublic, ExpenseUpdate


class ReviewQueuePublic(BaseModel):
    """Summary shape for ``GET /review-queue`` list view."""

    model_config = ConfigDict(from_attributes=True)

    review_id: uuid.UUID
    expense_id: uuid.UUID
    review_reasons: list[ReviewReasonCode]
    status: ReviewQueueStatus
    opened_at: datetime
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolution_notes: str | None


class ReviewQueueDetail(BaseModel):
    """Detail shape for ``GET /review-queue/{id}``.

    Bundles the queue row, the expense being reviewed, and — when the
    queue entry was created because of a duplicate suspicion — the
    earlier expense that produced the flag. Ambiguous-match metadata
    is not persisted and thus not surfaced here.
    """

    model_config = ConfigDict(from_attributes=True)

    review_id: uuid.UUID
    expense_id: uuid.UUID
    review_reasons: list[ReviewReasonCode]
    status: ReviewQueueStatus
    opened_at: datetime
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolution_notes: str | None
    expense: ExpenseDetailPublic
    duplicate_of: ExpenseDetailPublic | None


class ResolveRequest(BaseModel):
    """Payload for ``POST /review-queue/{id}/resolve``.

    ``expense_patch`` (optional) lets the admin update any expense
    fields while approving; it's applied before the status flips to
    reviewed. ``notes`` is stored on the queue row and included in the
    audit log if present.
    """

    expense_patch: ExpenseUpdate | None = None
    notes: str | None = Field(default=None, max_length=500)


class RejectRequest(BaseModel):
    """Payload for ``POST /review-queue/{id}/reject``.

    ``notes`` is stored on the queue row and included in the audit log.
    """

    notes: str | None = Field(default=None, max_length=500)
