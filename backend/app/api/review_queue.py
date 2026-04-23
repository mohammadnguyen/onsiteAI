"""Review-queue HTTP routes for Phase 2 Task T-N.

Thin layer that forwards to :mod:`app.services.review_queue` and maps
the service's domain exceptions onto HTTP status codes:

* :class:`ReviewQueueNotFound` -> 404
* :class:`ReviewQueueAlreadyClosed` -> 409
* FK-validation ``ValueError`` from a bad ``expense_patch`` -> 422

Auth policy:

* All four routes are admin-only (``Depends(require_admin)``). The
  review queue is an admin tool — contributors have no reason to read
  or write it directly, and T-M's audit trail already surfaces any
  state changes back to them through ``GET /expenses/{id}/audit`` if
  they're ever promoted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin
from app.models import ReviewQueueStatus, User
from app.schemas.expense import ExpenseDetailPublic
from app.schemas.review_queue import (
    RejectRequest,
    ResolveRequest,
    ReviewQueueDetail,
    ReviewQueuePublic,
)
from app.services import review_queue as svc

router = APIRouter(tags=["review-queue"])


@router.get(
    "",
    response_model=list[ReviewQueuePublic],
    status_code=status.HTTP_200_OK,
)
async def list_queue(
    status_filter: ReviewQueueStatus = Query(default=ReviewQueueStatus.open, alias="status"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewQueuePublic]:
    """List queue rows ordered by ``opened_at`` ASC (admin-only).

    The ``status`` query param is aliased onto a Python-safe
    ``status_filter`` parameter name to avoid shadowing
    :mod:`fastapi.status`.
    """
    rows = await svc.list_open(db, status=status_filter)
    return [ReviewQueuePublic.model_validate(r) for r in rows]


@router.get(
    "/{review_id}",
    response_model=ReviewQueueDetail,
    status_code=status.HTTP_200_OK,
)
async def get_queue_detail(
    review_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ReviewQueueDetail:
    """Return the queue row + nested expense + duplicate-of expense (admin-only)."""
    try:
        row, expense, duplicate_of = await svc.get(db, review_id=review_id)
    except svc.ReviewQueueNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review queue entry not found",
        ) from exc

    return ReviewQueueDetail(
        review_id=row.review_id,
        expense_id=row.expense_id,
        review_reasons=list(row.review_reasons),
        status=row.status,
        opened_at=row.opened_at,
        resolved_by_user_id=row.resolved_by_user_id,
        resolved_at=row.resolved_at,
        resolution_notes=row.resolution_notes,
        expense=ExpenseDetailPublic.model_validate(expense),
        duplicate_of=(
            ExpenseDetailPublic.model_validate(duplicate_of) if duplicate_of is not None else None
        ),
    )


@router.post(
    "/{review_id}/resolve",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def resolve_queue(
    review_id: uuid.UUID,
    body: ResolveRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Approve a queue entry (admin-only).

    The service runs the expense-update + queue-close + audit-write
    inside the single request-scoped transaction ``get_db`` owns. Any
    failure rolls back the whole unit; on success they all commit.
    """
    try:
        await svc.resolve(
            db,
            admin=admin,
            review_id=review_id,
            expense_patch=body.expense_patch,
            notes=body.notes,
        )
    except svc.ReviewQueueNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review queue entry not found",
        ) from exc
    except svc.ReviewQueueAlreadyClosed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review queue entry already {exc.current_status.value}",
        ) from exc
    except ValueError as exc:
        # Bad FK reference in expense_patch -> 422.
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{review_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_queue(
    review_id: uuid.UUID,
    body: RejectRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Reject a queue entry (admin-only). Atomic like :func:`resolve_queue`."""
    try:
        await svc.reject(
            db,
            admin=admin,
            review_id=review_id,
            notes=body.notes,
        )
    except svc.ReviewQueueNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review queue entry not found",
        ) from exc
    except svc.ReviewQueueAlreadyClosed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review queue entry already {exc.current_status.value}",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
