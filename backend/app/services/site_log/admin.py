"""Admin reset, finalize, and job attribution.

Transitions driven by an operator or by the completion of uploads, rather
than by the capture itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import EvidenceStatus
from app.models.job import Job
from app.models.site_log import (
    AttachmentState,
    CaptureStatus,
    SiteLogAuditAction,
    SiteLogEvent,
    SiteLogEventAttachment,
)
from app.models.user import User, UserRole
from app.services.site_log_access import can_read_event

from .core import (
    RESET_MIN_AGE,
    _audit,
    _evidence_audit,
    _lock_attachment,
    _lock_attachments,
    _lock_event,
    _lock_evidence_rows,
    _lock_scope,
)
from .errors import (
    SiteLogAlreadyAssigned,
    SiteLogForbidden,
    SiteLogNotFound,
    SiteLogNothingToReset,
    SiteLogNotReady,
    SiteLogReasonRequired,
    SiteLogResetNotEligible,
    SiteLogSameJob,
)
from .views import EventView, _target_job, _view


async def reset_attachment(
    db: AsyncSession,
    *,
    admin: User,
    event_id: uuid.UUID,
    attachment_client_id: uuid.UUID,
    reason: str | None,
    now: datetime,
) -> SiteLogEventAttachment:
    """Row 8: pending→failed on manifest AND Evidence, atomically, under
    the global lock order. Admin only, non-empty reason, age ≥ 15 min.

    Denial order (founder ruling A): unknown / cross-tenant / unreadable
    event → not found (existence hidden); readable event, non-admin →
    forbidden; then validation and state conflicts.
    """
    async with _lock_scope(db):
        event = await _lock_event(db, event_id)
        job = None if event is None or event.job_id is None else await db.get(Job, event.job_id)
        if event is None or not can_read_event(admin, event, job):
            raise SiteLogNotFound()
        if admin.role != UserRole.admin:
            raise SiteLogForbidden()
        att = await _lock_attachment(db, event, attachment_client_id)
        if att is None:
            raise SiteLogNotFound()
        if not reason or not reason.strip():
            raise SiteLogReasonRequired()
        if att.state is not AttachmentState.pending:
            raise SiteLogNothingToReset()
        age = now - att.updated_at
        if age < RESET_MIN_AGE:
            raise SiteLogResetNotEligible()
        ev_rows = await _lock_evidence_rows(db, [att.evidence_id])
        att.state = AttachmentState.failed
        for ev in ev_rows:
            ev.status = EvidenceStatus.failed
            db.add(
                _evidence_audit(
                    ev.evidence_id, admin.user_id, "failed",
                    {"reason": "admin_reset", "attempt_no": att.upload_attempt_no},
                )
            )
        db.add(
            _audit(
                event.site_log_event_id, event.tenant_id, admin.user_id,
                SiteLogAuditAction.attachment_state_changed,
                {"attachment_client_id": str(attachment_client_id),
                 "from": "pending", "to": "failed", "admin_reset": True,
                 "reason": reason.strip(), "attempt_no": att.upload_attempt_no,
                 "age_seconds": int(age.total_seconds())},
            )
        )
    await db.commit()
    return att


# --------------------------------------------------------------- finalize


async def finalize_capture(
    db: AsyncSession, *, user: User, event_id: uuid.UUID
) -> EventView:
    """complete / repairable partial_failed / not-ready; idempotent."""
    async with _lock_scope(db) as sp:
        event = await _lock_event(db, event_id)
        job = None if event is None or event.job_id is None else await db.get(Job, event.job_id)
        if event is None or not can_read_event(user, event, job):
            raise SiteLogNotFound()
        if user.role != UserRole.admin and event.author_user_id != user.user_id:
            raise SiteLogNotFound()
        atts = await _lock_attachments(db, event)
        states = {str(a.attachment_client_id): a.state.value for a in atts}
        in_flight = [
            a
            for a in atts
            if a.state in (AttachmentState.awaiting_upload, AttachmentState.pending)
        ]
        if in_flight:
            raise SiteLogNotReady(states)
        target = (
            CaptureStatus.complete
            if all(a.state is AttachmentState.stored for a in atts)
            else CaptureStatus.partial_failed
        )
        if event.capture_status is target:
            await sp.rollback()  # same-state replay: no write, no audit
            return await _view(db, event)
        prev = event.capture_status
        event.capture_status = target
        db.add(
            _audit(
                event.site_log_event_id, event.tenant_id, user.user_id,
                SiteLogAuditAction.finalized,
                {"from": prev.value, "to": target.value, "attachment_states": states},
            )
        )
    await db.commit()
    return await _view(db, event)


# ------------------------------------------------------- job attribution


async def _sync_job(
    db: AsyncSession,
    *,
    actor: User,
    event: SiteLogEvent,
    new_job: Job,
    action: SiteLogAuditAction,
    reason: str | None,
) -> None:
    """Event + every bound Evidence row, global order. Writes only — the
    caller owns the lock scope and the commit."""
    old_job_id = event.job_id
    atts = await _lock_attachments(db, event)
    ev_rows = await _lock_evidence_rows(db, [a.evidence_id for a in atts if a.evidence_id])
    event.job_id = new_job.job_id
    for ev in ev_rows:
        ev.job_id = new_job.job_id
        db.add(
            _evidence_audit(
                ev.evidence_id, actor.user_id,
                "job_linked" if old_job_id is None else "job_relinked",
                {"old_job_id": str(old_job_id) if old_job_id else None,
                 "new_job_id": str(new_job.job_id), "reason": reason,
                 "via_site_log_event": str(event.site_log_event_id)},
            )
        )
    db.add(
        _audit(
            event.site_log_event_id, event.tenant_id, actor.user_id, action,
            {"old_job_id": str(old_job_id) if old_job_id else None,
             "new_job_id": str(new_job.job_id), "reason": reason,
             "evidence_rows_synced": len(ev_rows)},
        )
    )


async def assign_job(
    db: AsyncSession, *, user: User, event_id: uuid.UUID, job_id: uuid.UUID
) -> EventView:
    """First assignment from unassigned — author or admin."""
    async with _lock_scope(db):
        event = await _lock_event(db, event_id)
        if event is None or not (
            user.role == UserRole.admin or event.author_user_id == user.user_id
        ):
            raise SiteLogNotFound()
        if event.job_id is not None:
            raise SiteLogAlreadyAssigned()
        job = await _target_job(db, job_id, user)
        await _sync_job(db, actor=user, event=event, new_job=job,
                        action=SiteLogAuditAction.job_assigned, reason=None)
    await db.commit()
    return await _view(db, event)


async def relink_job(
    db: AsyncSession, *, user: User, event_id: uuid.UUID,
    job_id: uuid.UUID, reason: str | None,
) -> EventView:
    """Reassignment — admin only, non-empty reason, same-job → 409.

    Denial order (founder ruling A): unreadable → not found; readable,
    non-admin → forbidden; then state and validation.
    """
    async with _lock_scope(db):
        event = await _lock_event(db, event_id)
        job_cur = (
            None if event is None or event.job_id is None else await db.get(Job, event.job_id)
        )
        if event is None or not can_read_event(user, event, job_cur):
            raise SiteLogNotFound()
        if user.role != UserRole.admin:
            raise SiteLogForbidden()
        if event.job_id is None:
            raise SiteLogAlreadyAssigned()  # nothing to relink: use assign
        if not reason or not reason.strip():
            raise SiteLogReasonRequired()
        if event.job_id == job_id:
            raise SiteLogSameJob()
        job = await _target_job(db, job_id, user)
        await _sync_job(db, actor=user, event=event, new_job=job,
                        action=SiteLogAuditAction.job_relinked, reason=reason.strip())
    await db.commit()
    return await _view(db, event)
