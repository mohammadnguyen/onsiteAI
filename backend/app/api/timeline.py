"""Job Timeline HTTP routes (PR 5 — non-attachment endpoints).

Thin layer that forwards to :mod:`app.services.timeline` and maps the
service's domain exceptions onto HTTP status codes:

* :class:`JobNotFoundForTimeline` / :class:`TimelineItemNotFound` /
  :class:`ChecklistItemNotFound` -> 404
* :class:`TimelinePermissionDenied` -> 403
* :class:`TimelineValidationError` -> 422
* :class:`InvalidTimelineCursor` -> 400

Auth policy: every endpoint requires an authenticated caller
(``get_current_user``) and nothing more at the boundary. ALL
authorization lives in the service — creator-or-admin writes, the
admin-only ``closed`` gate, job scoping. Deliberately no admin
dependency and no caller-inspection here: the existence check runs
before the permission check in the service, so probing an unknown or
soft-deleted id returns 404 (never a 403 that would confirm the id
exists), and the state machine cannot drift between layers.

Spans two URL roots (``/jobs/{job_id}/timeline`` + ``/timeline/...``
+ ``/jobs/{job_id}/checklist``), so the router is included unprefixed
with absolute paths — same composition pattern as labour.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import IssueStatus, TimelineItemType, User
from app.schemas.timeline import (
    AttachmentConfirm,
    AttachmentPublic,
    AttachmentUploadRequest,
    AttachmentUploadResponse,
    ChecklistItemPublic,
    ChecklistToggle,
    IssueStatusUpdate,
    TimelineItemCreate,
    TimelineItemDetailPublic,
    TimelineItemListResponse,
    TimelineItemPublic,
    TimelineItemUpdate,
)
from app.services import timeline as svc
from app.services.timeline_storage import StorageNotConfigured

router = APIRouter(tags=["timeline"])


def _map_job_not_found(exc: svc.JobNotFoundForTimeline) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
    )


def _map_item_not_found(exc: svc.TimelineItemNotFound) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Timeline item not found"
    )


def _map_permission_denied(exc: svc.TimelinePermissionDenied) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
    )


def _map_validation(exc: svc.TimelineValidationError) -> HTTPException:
    # Literal 422 (as in app/api/expenses.py): starlette deprecated the
    # HTTP_422_UNPROCESSABLE_ENTITY constant name.
    return HTTPException(status_code=422, detail=exc.detail)


def _map_attachment_not_found(exc: svc.AttachmentNotFound) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
    )


def _map_storage_unconfigured(exc: StorageNotConfigured) -> HTTPException:
    # Operator misconfiguration (dev without a bucket), not a client
    # error: 503 says "try again once the service is configured".
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail
    )


@router.get(
    "/jobs/{job_id}/timeline",
    response_model=TimelineItemListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_timeline_endpoint(
    job_id: uuid.UUID,
    item_type: TimelineItemType | None = None,
    issue_status: IssueStatus | None = Query(default=None, alias="status"),
    date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineItemListResponse:
    """List a job's timeline, newest ``occurred_at`` first (team-visible).

    ``cursor`` is the opaque ``next_cursor`` from the previous page —
    echo it back verbatim; an undecodable cursor returns 400.
    ``date_from`` / ``date_to`` must carry a UTC offset (naive
    datetimes 422, same contract as ``occurred_at`` on write).
    """
    try:
        items, next_cursor = await svc.list_timeline_items(
            db,
            job_id=job_id,
            current_user=current_user,
            item_type=item_type,
            status=issue_status,
            date_from=date_from,
            date_to=date_to,
            cursor=cursor,
            limit=limit,
        )
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.InvalidTimelineCursor as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc
    return TimelineItemListResponse(
        items=[TimelineItemPublic.model_validate(i) for i in items],
        next_cursor=next_cursor,
    )


@router.post(
    "/jobs/{job_id}/timeline",
    response_model=TimelineItemPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_timeline_item_endpoint(
    job_id: uuid.UUID,
    body: TimelineItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineItemPublic:
    """Create a timeline record on the job, owned by the caller."""
    try:
        item = await svc.create_timeline_item(
            db, job_id=job_id, current_user=current_user, payload=body
        )
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelineValidationError as exc:
        raise _map_validation(exc) from exc
    return TimelineItemPublic.model_validate(item)


@router.get(
    "/timeline/{item_id}",
    response_model=TimelineItemDetailPublic,
    status_code=status.HTTP_200_OK,
)
async def get_timeline_item_endpoint(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineItemDetailPublic:
    """Fetch one item with its live attachments (detail view)."""
    try:
        item = await svc.get_timeline_item(
            db, item_id=item_id, current_user=current_user
        )
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    return TimelineItemDetailPublic.model_validate(item)


@router.patch(
    "/timeline/{item_id}",
    response_model=TimelineItemPublic,
    status_code=status.HTTP_200_OK,
)
async def update_timeline_item_endpoint(
    item_id: uuid.UUID,
    body: TimelineItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineItemPublic:
    """Partial update; ownership + issue-title invariants in the service."""
    try:
        item = await svc.update_timeline_item(
            db, item_id=item_id, current_user=current_user, payload=body
        )
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelinePermissionDenied as exc:
        raise _map_permission_denied(exc) from exc
    except svc.TimelineValidationError as exc:
        raise _map_validation(exc) from exc
    return TimelineItemPublic.model_validate(item)


@router.delete(
    "/timeline/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_timeline_item_endpoint(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-delete (creator or admin; enforced in the service)."""
    try:
        await svc.soft_delete_timeline_item(
            db, item_id=item_id, current_user=current_user
        )
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelinePermissionDenied as exc:
        raise _map_permission_denied(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/timeline/{item_id}/status",
    response_model=TimelineItemPublic,
    status_code=status.HTTP_200_OK,
)
async def change_issue_status_endpoint(
    item_id: uuid.UUID,
    body: IssueStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineItemPublic:
    """Transition an issue's status.

    The two-stage sign-off machine (who may enter/leave ``closed``,
    which transitions exist at all) is enforced entirely in the
    service — this endpoint carries no gate of its own.
    """
    try:
        item = await svc.change_issue_status(
            db,
            item_id=item_id,
            current_user=current_user,
            new_status=body.status,
        )
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelinePermissionDenied as exc:
        raise _map_permission_denied(exc) from exc
    except svc.TimelineValidationError as exc:
        raise _map_validation(exc) from exc
    return TimelineItemPublic.model_validate(item)


@router.post(
    "/timeline/{item_id}/attachments",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_attachment_upload_endpoint(
    item_id: uuid.UUID,
    body: AttachmentUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentUploadResponse:
    """Insert a pending attachment row and return its presigned PUT URL.

    The client PUTs the bytes directly to object storage (with the
    exact ``Content-Type`` it declared here — it is signature-bound),
    then calls the confirm endpoint.
    """
    try:
        attachment, presigned_url = await svc.create_attachment_upload(
            db, item_id=item_id, current_user=current_user, payload=body
        )
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except StorageNotConfigured as exc:
        raise _map_storage_unconfigured(exc) from exc
    return AttachmentUploadResponse(
        attachment_id=attachment.attachment_id,
        storage_key=attachment.storage_key,
        presigned_url=presigned_url,
    )


@router.post(
    "/timeline/attachments/{attachment_id}/confirm",
    response_model=AttachmentPublic,
    status_code=status.HTTP_200_OK,
)
async def confirm_attachment_endpoint(
    attachment_id: uuid.UUID,
    body: AttachmentConfirm,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentPublic:
    """Record final object metadata; pending → confirmed (uploader/admin)."""
    try:
        attachment = await svc.confirm_attachment(
            db,
            attachment_id=attachment_id,
            current_user=current_user,
            payload=body,
        )
    except svc.AttachmentNotFound as exc:
        raise _map_attachment_not_found(exc) from exc
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelinePermissionDenied as exc:
        raise _map_permission_denied(exc) from exc
    return AttachmentPublic.model_validate(attachment)


@router.get(
    "/timeline/attachments/{attachment_id}",
    response_model=AttachmentPublic,
    status_code=status.HTTP_200_OK,
)
async def get_attachment_endpoint(
    attachment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentPublic:
    """Attachment metadata plus a fresh short-lived presigned GET URL."""
    try:
        attachment, download_url = await svc.get_attachment_download(
            db, attachment_id=attachment_id, current_user=current_user
        )
    except svc.AttachmentNotFound as exc:
        raise _map_attachment_not_found(exc) from exc
    except svc.TimelineItemNotFound as exc:
        raise _map_item_not_found(exc) from exc
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.TimelineValidationError as exc:
        raise _map_validation(exc) from exc
    except StorageNotConfigured as exc:
        raise _map_storage_unconfigured(exc) from exc
    return AttachmentPublic.model_validate(attachment).model_copy(
        update={"download_url": download_url}
    )


@router.get(
    "/jobs/{job_id}/checklist",
    response_model=list[ChecklistItemPublic],
    status_code=status.HTTP_200_OK,
)
async def list_checklist_endpoint(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChecklistItemPublic]:
    """The job's live checklist, in ``sort_order``."""
    try:
        rows = await svc.list_checklist_items(
            db, job_id=job_id, current_user=current_user
        )
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    return [ChecklistItemPublic.model_validate(r) for r in rows]


@router.patch(
    "/jobs/{job_id}/checklist/{checklist_item_id}/toggle",
    response_model=ChecklistItemPublic,
    status_code=status.HTTP_200_OK,
)
async def toggle_checklist_endpoint(
    job_id: uuid.UUID,
    checklist_item_id: uuid.UUID,
    body: ChecklistToggle,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChecklistItemPublic:
    """Set a checklist item's done state (explicit target; idempotent)."""
    try:
        row = await svc.toggle_checklist_item(
            db,
            job_id=job_id,
            checklist_item_id=checklist_item_id,
            current_user=current_user,
            is_done=body.is_done,
        )
    except svc.JobNotFoundForTimeline as exc:
        raise _map_job_not_found(exc) from exc
    except svc.ChecklistItemNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checklist item not found",
        ) from exc
    return ChecklistItemPublic.model_validate(row)
