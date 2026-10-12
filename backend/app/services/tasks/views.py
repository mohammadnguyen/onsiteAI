"""Reading tasks: access resolution, the view model, and the read entry
points. No writes.

A view carries what a response needs and nothing more. In particular it
never carries the proposed title: that lives only in the audit, which no
endpoint returns (D3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.site_log import SiteLogEvent, SiteLogEventRevision
from app.models.task import Task, TaskAuditAction, TaskAuditLog, TaskStatus
from app.models.user import User
from app.services.site_log_access import TENANT_ID, can_read_event

from .access import (
    can_assign_others,
    can_change_job,
    can_change_status,
    can_edit_task,
    can_set_job,
    can_view_task,
    visible_tasks_clause,
)
from .errors import TaskNotFound, TaskSourceNotFound

TASK_PAGE_DEFAULT = 20
TASK_PAGE_MAX = 50

# Every read here is taken as the row stands in the database, never as an
# identity-map copy left from earlier in the same session.
_FRESH = {"populate_existing": True}


@dataclass(frozen=True)
class TaskPermissions:
    """What the caller may do with this task, computed on the server so the
    app shows controls without restating the rules."""

    change_status: bool
    edit: bool
    assign_others: bool
    set_job: bool
    change_job: bool


@dataclass
class TaskView:
    task: Task
    job: Job | None
    assignee: User | None
    creator: User | None
    # Whether the caller may open the source entry. False: the app says so
    # and the link meets the entry's own 404.
    source_readable: bool
    # The entry's CURRENT job, only when the caller may read the entry, so
    # the app can note "the source entry is now under another job".
    source_job_id: uuid.UUID | None
    permissions: TaskPermissions


def _permissions(user: User, task: Task) -> TaskPermissions:
    return TaskPermissions(
        change_status=can_change_status(user, task),
        edit=can_edit_task(user, task),
        assign_others=can_assign_others(user),
        set_job=can_set_job(user, task),
        change_job=can_change_job(user, task),
    )


async def build_views(
    db: AsyncSession, user: User, tasks: list[Task]
) -> list[TaskView]:
    """Views for already-visible tasks, with batched lookups."""
    if not tasks:
        return []
    event_ids = {t.source_site_log_event_id for t in tasks}
    events = {
        e.site_log_event_id: e
        for e in (
            await db.execute(
                select(SiteLogEvent)
                .where(
                    SiteLogEvent.site_log_event_id.in_(event_ids),
                    SiteLogEvent.tenant_id == TENANT_ID,
                )
                .execution_options(**_FRESH)
            )
        ).scalars()
    }
    job_ids = {t.job_id for t in tasks if t.job_id} | {
        e.job_id for e in events.values() if e.job_id
    }
    jobs: dict[uuid.UUID, Job] = {}
    if job_ids:
        jobs = {
            j.job_id: j
            for j in (
                await db.execute(select(Job).where(Job.job_id.in_(job_ids)))
            ).scalars()
        }
    user_ids = {t.created_by_user_id for t in tasks} | {
        t.assignee_user_id for t in tasks if t.assignee_user_id
    }
    users = {
        u.user_id: u
        for u in (
            await db.execute(select(User).where(User.user_id.in_(user_ids)))
        ).scalars()
    }

    views = []
    for t in tasks:
        event = events.get(t.source_site_log_event_id)
        event_job = jobs.get(event.job_id) if event and event.job_id else None
        readable = event is not None and can_read_event(user, event, event_job)
        views.append(
            TaskView(
                task=t,
                job=jobs.get(t.job_id) if t.job_id else None,
                assignee=users.get(t.assignee_user_id) if t.assignee_user_id else None,
                creator=users.get(t.created_by_user_id),
                source_readable=readable,
                source_job_id=event.job_id if readable and event is not None else None,
                permissions=_permissions(user, t),
            )
        )
    return views


async def build_view(db: AsyncSession, user: User, task: Task) -> TaskView:
    return (await build_views(db, user, [task]))[0]


async def load_visible_task(
    db: AsyncSession, user: User, task_id: uuid.UUID, *, lock: bool = False
) -> tuple[Task, Job | None]:
    """The task and its job, or TaskNotFound when it is unknown or not
    visible to the caller (existence hidden). ``lock`` takes a row lock;
    the caller then owns the transaction."""
    q = (
        select(Task)
        .where(Task.task_id == task_id, Task.tenant_id == TENANT_ID)
        .execution_options(**_FRESH)
    )
    if lock:
        q = q.with_for_update()
    task = (await db.execute(q)).scalar_one_or_none()
    job = None
    if task is not None and task.job_id is not None:
        job = await db.get(Job, task.job_id)
    if task is None or not can_view_task(user, task, job):
        raise TaskNotFound()
    return task, job


async def readable_source(
    db: AsyncSession, user: User, event_id: uuid.UUID
) -> SiteLogEvent:
    """The Site Log entry, or TaskSourceNotFound when the caller may not
    read it. The entry's own rule applies unchanged."""
    event = (
        await db.execute(
            select(SiteLogEvent)
            .where(
                SiteLogEvent.site_log_event_id == event_id,
                SiteLogEvent.tenant_id == TENANT_ID,
            )
            .execution_options(**_FRESH)
        )
    ).scalar_one_or_none()
    job = None
    if event is not None and event.job_id is not None:
        job = await db.get(Job, event.job_id)
    if event is None or not can_read_event(user, event, job):
        raise TaskSourceNotFound()
    return event


async def current_revision(
    db: AsyncSession, event: SiteLogEvent
) -> SiteLogEventRevision:
    q = (
        select(SiteLogEventRevision)
        .where(
            SiteLogEventRevision.site_log_event_id == event.site_log_event_id,
            SiteLogEventRevision.tenant_id == event.tenant_id,
        )
        .order_by(SiteLogEventRevision.revision_no.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one()


async def created_fingerprint(db: AsyncSession, task: Task) -> str | None:
    q = select(TaskAuditLog).where(
        TaskAuditLog.task_id == task.task_id,
        TaskAuditLog.action == TaskAuditAction.created.value,
    )
    row = (await db.execute(q)).scalar_one_or_none()
    return None if row is None else row.changed_fields.get("create_fingerprint")


# ------------------------------------------------------------------ reads


async def get_task(db: AsyncSession, user: User, task_id: uuid.UUID) -> TaskView:
    task, _ = await load_visible_task(db, user, task_id)
    return await build_view(db, user, task)


async def list_tasks(
    db: AsyncSession,
    user: User,
    *,
    status: TaskStatus | None = None,
    mine: Literal["assigned", "created"] | None = None,
    job_id: uuid.UUID | None = None,
    create_client_id: uuid.UUID | None = None,
    limit: int = TASK_PAGE_DEFAULT,
    offset: int = 0,
) -> list[TaskView]:
    """Tasks the caller may view, newest first, one capped page at a time.

    ``create_client_id`` narrows to the caller's OWN create with that key —
    how the app learns whether a create whose answer was lost went through.
    """
    q = select(Task).where(Task.tenant_id == TENANT_ID)
    clause = visible_tasks_clause(user)
    if clause is not None:
        q = q.where(clause)
    if create_client_id is not None:
        q = q.where(
            Task.created_by_user_id == user.user_id,
            Task.create_client_id == create_client_id,
        )
    if status is not None:
        q = q.where(Task.status == status)
    if mine == "assigned":
        q = q.where(Task.assignee_user_id == user.user_id)
    elif mine == "created":
        q = q.where(Task.created_by_user_id == user.user_id)
    if job_id is not None:
        q = q.where(Task.job_id == job_id)
    q = (
        q.order_by(Task.created_at.desc(), Task.task_id.desc())
        .limit(max(1, min(limit, TASK_PAGE_MAX)))
        .offset(max(0, offset))
        .execution_options(**_FRESH)
    )
    tasks = list((await db.execute(q)).scalars().all())
    return await build_views(db, user, tasks)


async def list_event_tasks(
    db: AsyncSession, user: User, event_id: uuid.UUID
) -> list[TaskView]:
    """The tasks made from one entry that the caller may view. The caller
    must be able to read the entry itself (404 otherwise)."""
    await readable_source(db, user, event_id)
    q = select(Task).where(
        Task.tenant_id == TENANT_ID,
        Task.source_site_log_event_id == event_id,
    )
    clause = visible_tasks_clause(user)
    if clause is not None:
        q = q.where(clause)
    q = q.order_by(Task.created_at.desc(), Task.task_id.desc()).execution_options(
        **_FRESH
    )
    tasks = list((await db.execute(q)).scalars().all())
    return await build_views(db, user, tasks)
