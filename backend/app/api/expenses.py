"""Expense HTTP routes for Phase 2 Task T-M.

Thin layer that forwards to :mod:`app.services.expenses` and maps the
service's domain exceptions onto HTTP status codes:

* :class:`ExpenseNotFound` -> 404
* :class:`EditForbidden` / :class:`DeleteForbidden` -> 403
* :class:`ExpenseValidationError` -> 422
* :class:`JobNotFoundForExpense` -> 422 (with a specific detail)

Auth policy:

* ``POST /expenses`` / ``POST /expenses/parse`` / ``GET /expenses`` /
  ``GET /expenses/{id}`` / ``PATCH /expenses/{id}`` — any authenticated
  caller. The service enforces mine-only and ownership rules for
  contributors.
* ``DELETE /expenses/{id}`` / ``GET /expenses/{id}/audit`` — admin-only
  at the boundary via ``Depends(require_admin)``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import ReceiptStatus, ReviewStatus, User
from app.schemas.expense import (
    AuditRow,
    ExpenseCreate,
    ExpenseCreateResponse,
    ExpenseDetailPublic,
    ExpenseListResponse,
    ExpensePublic,
    ExpenseUpdate,
    ParsePreview,
    ParsePreviewRequest,
)
from app.services import expenses as svc

router = APIRouter(tags=["expenses"])


def _map_validation(exc: svc.ExpenseValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.detail)


def _map_job_not_found(exc: svc.JobNotFoundForExpense) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail="Job could not be identified",
    )


@router.post(
    "",
    response_model=ExpenseCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_expense_endpoint(
    body: ExpenseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseCreateResponse:
    """Create an expense from raw text or structured fields.

    The service runs the parser when ``raw_input_text`` is supplied
    and merges any explicit structured overrides on top of the parser
    draft (structured wins).
    """
    try:
        expense, diagnostics = await svc.create_expense(db, entered_by=current_user, payload=body)
    except svc.ExpenseValidationError as exc:
        raise _map_validation(exc) from exc
    except svc.JobNotFoundForExpense as exc:
        raise _map_job_not_found(exc) from exc

    return ExpenseCreateResponse(
        expense=ExpensePublic.model_validate(expense),
        parse=diagnostics,
    )


@router.post(
    "/parse",
    response_model=ParsePreview,
    status_code=status.HTTP_200_OK,
)
async def preview_parse_endpoint(
    body: ParsePreviewRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ParsePreview:
    """Run the parser and return draft + diagnostics. Does not persist."""
    return await svc.preview_parse(
        db,
        entered_by=current_user,
        raw_text=body.raw_input_text,
        expense_date=body.expense_date,
        expense_type=body.expense_type,
    )


@router.get(
    "",
    response_model=ExpenseListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_expenses_endpoint(
    job_id: uuid.UUID | None = None,
    review_status: ReviewStatus | None = Query(default=None, alias="status"),
    mine: int = 0,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    receipt_status: ReceiptStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseListResponse:
    """List expenses (contributors are restricted to their own rows)."""
    rows, next_cursor = await svc.list_expenses(
        db,
        current_user=current_user,
        mine=bool(mine),
        job_id=job_id,
        status=review_status,
        from_date=from_date,
        to_date=to_date,
        receipt_status=receipt_status,
        limit=limit,
        cursor=cursor,
    )
    return ExpenseListResponse(
        items=[ExpensePublic.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/{expense_id}",
    response_model=ExpenseDetailPublic,
    status_code=status.HTTP_200_OK,
)
async def get_expense_endpoint(
    expense_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseDetailPublic:
    """Fetch a single expense with nested supplier + category + current review reasons.

    ``review_reasons`` reflects the current ``expense_review_queue``
    row's reasons array, regardless of queue status (``open``,
    ``resolved`` or ``rejected``). Returns ``[]`` if no queue row
    exists. This is NOT a historical audit trail — see
    ``GET /expenses/{id}/audit`` for that.

    ``pending_review_queue_id`` is the ``review_id`` of the
    currently-actionable queue row IFF its status is ``open``. It is
    ``None`` for resolved / rejected / absent queue rows. Mobile uses
    this as the sole visibility gate for Approve / Reject buttons so
    stale resolved/rejected rows never expose those actions.
    """
    try:
        expense, reasons, pending_review_queue_id = await svc.get_expense_with_reasons(
            db, current_user=current_user, expense_id=expense_id
        )
    except svc.ExpenseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        ) from exc
    except svc.EditForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail) from exc
    return ExpenseDetailPublic.model_validate(expense).model_copy(
        update={
            "review_reasons": reasons,
            "pending_review_queue_id": pending_review_queue_id,
        }
    )


@router.patch(
    "/{expense_id}",
    response_model=ExpensePublic,
    status_code=status.HTTP_200_OK,
)
async def update_expense_endpoint(
    expense_id: uuid.UUID,
    body: ExpenseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpensePublic:
    """Partial update with RBAC + audit rules enforced in the service."""
    try:
        expense = await svc.update_expense(
            db,
            current_user=current_user,
            expense_id=expense_id,
            patch=body,
        )
    except svc.ExpenseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        ) from exc
    except svc.EditForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail) from exc
    except svc.ExpenseValidationError as exc:
        raise _map_validation(exc) from exc
    except svc.JobNotFoundForExpense as exc:
        raise _map_job_not_found(exc) from exc
    return ExpensePublic.model_validate(expense)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_expense_endpoint(
    expense_id: uuid.UUID,
    reason: str | None = Query(default=None, max_length=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Admin-only soft delete: sets review_status=rejected + audits."""
    try:
        await svc.delete_expense(db, admin=admin, expense_id=expense_id, reason=reason)
    except svc.ExpenseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        ) from exc
    except svc.DeleteForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{expense_id}/audit",
    response_model=list[AuditRow],
    status_code=status.HTTP_200_OK,
)
async def get_audit_endpoint(
    expense_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AuditRow]:
    """Admin-only audit trail, newest first."""
    try:
        rows = await svc.get_audit(db, admin=admin, expense_id=expense_id)
    except svc.ExpenseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found"
        ) from exc
    except svc.DeleteForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail) from exc
    return [AuditRow.model_validate(r) for r in rows]
