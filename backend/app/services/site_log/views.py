"""Reading an event: access resolution, the view model, and the read API.

No writes. The write clusters use :func:`_view` to shape their result and
:func:`_readable_event` / :func:`_target_job` to resolve access.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.site_log import (
    SiteLogAuditAction,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
    SiteLogEventRevision,
)
from app.models.user import User, UserRole
from app.services.site_log_access import TENANT_ID, can_read_event, can_read_job

from .core import _FRESH
from .errors import SiteLogJobCompleted, SiteLogJobNotFound, SiteLogNotFound


async def _readable_event(
    db: AsyncSession, user: User, event_id: uuid.UUID
) -> tuple[SiteLogEvent, Job | None]:
    q = (
        select(SiteLogEvent)
        .where(
            SiteLogEvent.site_log_event_id == event_id,
            SiteLogEvent.tenant_id == TENANT_ID,
        )
        .execution_options(**_FRESH)
    )
    event = (await db.execute(q)).scalar_one_or_none()
    job = None
    if event is not None and event.job_id is not None:
        job = await db.get(Job, event.job_id)
    if event is None or not can_read_event(user, event, job):
        raise SiteLogNotFound()
    return event, job


async def _current_revision(
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


async def _attachments(
    db: AsyncSession, event: SiteLogEvent
) -> list[SiteLogEventAttachment]:
    q = (
        select(SiteLogEventAttachment)
        .where(
            SiteLogEventAttachment.site_log_event_id == event.site_log_event_id,
            SiteLogEventAttachment.tenant_id == event.tenant_id,
        )
        .order_by(SiteLogEventAttachment.attachment_id)
        .execution_options(**_FRESH)
    )
    return list((await db.execute(q)).scalars().all())


@dataclass
class EventView:
    event: SiteLogEvent
    revision: SiteLogEventRevision
    attachments: list[SiteLogEventAttachment]


async def _view(db: AsyncSession, event: SiteLogEvent) -> EventView:
    return EventView(
        event=event,
        revision=await _current_revision(db, event),
        attachments=await _attachments(db, event),
    )


async def _fingerprint_of(db: AsyncSession, event: SiteLogEvent) -> str | None:
    q = (
        select(SiteLogEventAuditLog)
        .where(
            SiteLogEventAuditLog.site_log_event_id == event.site_log_event_id,
            SiteLogEventAuditLog.action == SiteLogAuditAction.created.value,
        )
        .limit(1)
    )
    row = (await db.execute(q)).scalar_one_or_none()
    return None if row is None else row.changed_fields.get("declaration_fingerprint")


async def _target_job(db: AsyncSession, job_id: uuid.UUID, user: User) -> Job:
    job = await db.get(Job, job_id)
    if job is None or not can_read_job(user, job):
        raise SiteLogJobNotFound()
    if job.status == JobStatus.completed:
        raise SiteLogJobCompleted()
    return job


# ------------------------------------------------------------------ reads


async def get_event(db: AsyncSession, user: User, event_id: uuid.UUID) -> EventView:
    event, _ = await _readable_event(db, user, event_id)
    return await _view(db, event)


async def list_job_events(
    db: AsyncSession, user: User, job_id: uuid.UUID
) -> list[EventView]:
    job = await db.get(Job, job_id)
    if job is None or not can_read_job(user, job):
        raise SiteLogJobNotFound()
    q = (
        select(SiteLogEvent)
        .where(SiteLogEvent.job_id == job_id, SiteLogEvent.tenant_id == TENANT_ID)
        .order_by(SiteLogEvent.created_at.desc())
    )
    events = list((await db.execute(q)).scalars().all())
    views = []
    for e in events:
        v = await _view(db, e)
        if not v.revision.withdrawn:  # A2b adds include_withdrawn
            views.append(v)
    return views


async def list_unassigned(db: AsyncSession, user: User) -> list[EventView]:
    q = (
        select(SiteLogEvent)
        .where(SiteLogEvent.job_id.is_(None), SiteLogEvent.tenant_id == TENANT_ID)
        .order_by(SiteLogEvent.created_at.asc())
    )
    if user.role != UserRole.admin:
        q = q.where(SiteLogEvent.author_user_id == user.user_id)
    events = list((await db.execute(q)).scalars().all())
    return [await _view(db, e) for e in events]
