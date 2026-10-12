"""Task endpoints (Site Log → Task, plan v2 §4).

Routes (absolute paths, included unprefixed like Site Log, because two of
them live under ``/site-log-events``):

* ``POST  /site-log-events/{event_id}/tasks`` — create (201) or replay (200).
* ``GET   /site-log-events/{event_id}/tasks`` — visible tasks from one entry.
* ``GET   /tasks`` — visible tasks; filters; ``create_client_id`` lookup.
* ``GET   /tasks/{task_id}``.
* ``PATCH /tasks/{task_id}``.

Routes map domain exceptions to status codes and hold no business or
transaction logic. Not visible → 404; visible but not allowed → 403.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.task import TaskStatus
from app.models.user import User
from app.schemas.task import (
    TaskCreateIn,
    TaskJobOut,
    TaskOut,
    TaskPatchIn,
    TaskPermissionsOut,
    TaskSourceOut,
    TaskUserOut,
)
from app.services import tasks as task_svc

router = APIRouter(tags=["tasks"])


def _user_out(user: User | None) -> TaskUserOut | None:
    if user is None:
        return None
    return TaskUserOut(user_id=user.user_id, full_name=user.full_name)


def _out(view: task_svc.TaskView) -> TaskOut:
    t = view.task
    job = view.job
    p = view.permissions
    return TaskOut(
        task_id=t.task_id,
        title=t.title,
        status=t.status,
        origin=t.origin,
        due_date=t.due_date,
        job=(
            TaskJobOut(
                job_id=job.job_id,
                job_name=job.job_name,
                job_code=job.job_code,
                status=job.status,
            )
            if job is not None
            else None
        ),
        assignee=_user_out(view.assignee),
        created_by=_user_out(view.creator),
        source=TaskSourceOut(
            site_log_event_id=t.source_site_log_event_id,
            revision_no=t.source_revision_no,
            readable=view.source_readable,
            job_id=view.source_job_id,
        ),
        permissions=TaskPermissionsOut(
            change_status=p.change_status,
            edit=p.edit,
            assign_others=p.assign_others,
            set_job=p.set_job,
            change_job=p.change_job,
        ),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


_STATUS = {
    task_svc.TaskNotFound: 404,
    task_svc.TaskSourceNotFound: 404,
    task_svc.TaskJobNotFound: 404,
    task_svc.TaskSourceWithdrawn: 409,
    task_svc.TaskFingerprintMismatch: 409,
    task_svc.TaskForbidden: 403,
    task_svc.TaskJobCompleted: 422,
    task_svc.TaskReasonRequired: 422,
    task_svc.TaskValidationError: 422,
}


def _map(exc: task_svc.TaskError) -> HTTPException:
    for cls, code in _STATUS.items():
        if isinstance(exc, cls):
            return HTTPException(status_code=code, detail=exc.message)
    return HTTPException(status_code=500, detail="Task error")


@router.post("/site-log-events/{event_id}/tasks", response_model=TaskOut)
async def create_task(
    event_id: uuid.UUID,
    payload: TaskCreateIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await task_svc.create_task(
            db,
            user=user,
            event_id=event_id,
            create_client_id=payload.create_client_id,
            title=payload.title,
            assignee_user_id=payload.assignee_user_id,
            due_date=payload.due_date,
        )
    except task_svc.TaskError as exc:
        raise _map(exc) from exc
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return _out(result.view)


@router.get("/site-log-events/{event_id}/tasks", response_model=list[TaskOut])
async def list_event_tasks(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return [_out(v) for v in await task_svc.list_event_tasks(db, user, event_id)]
    except task_svc.TaskError as exc:
        raise _map(exc) from exc


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    status_filter: TaskStatus | None = Query(None, alias="status"),
    mine: Literal["assigned", "created"] | None = Query(None),
    job_id: uuid.UUID | None = Query(None),
    create_client_id: uuid.UUID | None = Query(None),
    limit: int = Query(task_svc.TASK_PAGE_DEFAULT, ge=1, le=task_svc.TASK_PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    views = await task_svc.list_tasks(
        db,
        user,
        status=status_filter,
        mine=mine,
        job_id=job_id,
        create_client_id=create_client_id,
        limit=limit,
        offset=offset,
    )
    return [_out(v) for v in views]


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _out(await task_svc.get_task(db, user, task_id))
    except task_svc.TaskError as exc:
        raise _map(exc) from exc


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskPatchIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sent = payload.model_fields_set
    changes = {
        f: getattr(payload, f) for f in sent if f in task_svc.UPDATABLE_FIELDS
    }
    try:
        view = await task_svc.update_task(
            db, user=user, task_id=task_id, changes=changes, reason=payload.reason
        )
    except task_svc.TaskError as exc:
        raise _map(exc) from exc
    return _out(view)
