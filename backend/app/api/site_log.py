"""Site Log capture endpoints (WP A A2a).

Routes (absolute paths — included unprefixed like evidence/labour
because the job-scoped listing lives under ``/jobs``):

* ``POST /site-log-events`` — declare (two-phase protocol phase 1).
* ``PUT  /site-log-events/{id}/attachments/{client_id}`` — upload bytes.
* ``POST /site-log-events/{id}/attachments/{client_id}/reset`` — admin.
* ``POST /site-log-events/{id}/finalize``.
* ``POST /site-log-events/{id}/assign-job`` / ``relink-job``.
* ``GET  /site-log-events/unassigned`` (declared before ``/{id}``).
* ``GET  /site-log-events/{id}``.
* ``GET  /jobs/{job_id}/site-log-events``.

Routes map domain exceptions to status codes and hold no business or
transaction logic. Denials: existence-hiding → 404; admin-only role
denial on a readable object → 403 "Admin only" (repo convention from
``app.deps.require_admin`` and the evidence relink precedent).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, get_sessionmaker
from app.deps import get_current_user
from app.models.user import User
from app.schemas.site_log import (
    AssignJobIn,
    AttachmentOut,
    CaptureDeclareIn,
    RelinkJobIn,
    ResetAttachmentIn,
    RevisionOut,
    SiteLogEventOut,
    UploadOut,
)
from app.services import evidence as evidence_service
from app.services import site_log as svc
from app.services.evidence_storage import (
    EvidenceStorage,
    EvidenceStorageError,
    get_evidence_storage,
)

router = APIRouter(tags=["site-log"])


def get_session_factory():
    """Fresh-session factory for Txn B retries (overridden in tests)."""
    return get_sessionmaker()


def _out(view: svc.EventView) -> SiteLogEventOut:
    e = view.event
    return SiteLogEventOut(
        site_log_event_id=e.site_log_event_id,
        author_user_id=e.author_user_id,
        job_id=e.job_id,
        job_state="unassigned" if e.job_id is None else "confirmed",
        capture_status=e.capture_status,
        created_at=e.created_at,
        revision=RevisionOut.model_validate(view.revision),
        attachments=[AttachmentOut.model_validate(a) for a in view.attachments],
    )


def _map(exc: svc.SiteLogError) -> HTTPException:
    m = {
        svc.SiteLogNotFound: (404, "Site log event not found"),
        svc.SiteLogJobNotFound: (404, "Job not found"),
        svc.SiteLogJobCompleted: (422, "Job is completed"),
        svc.SiteLogFingerprintMismatch: (
            409, "capture_client_id reused with a different declaration"),
        svc.SiteLogUploadInProgress: (409, "Upload in progress"),
        svc.SiteLogAttemptSuperseded: (409, "Upload attempt superseded"),
        svc.SiteLogMediaMismatch: (422, "MIME type disagrees with declared media type"),
        svc.SiteLogTooLarge: (413, "Upload exceeds the evidence size limit"),
        svc.SiteLogResetNotEligible: (409, "Pending attempt is not yet eligible for reset"),
        svc.SiteLogNothingToReset: (409, "Attachment is not pending"),
        svc.SiteLogForbidden: (403, "Admin only"),
        svc.SiteLogReasonRequired: (422, "A non-empty reason is required"),
        svc.SiteLogSameJob: (409, "Event is already linked to this job"),
        svc.SiteLogAlreadyAssigned: (409, "Job assignment state does not permit this action"),
    }
    for cls, (code, detail) in m.items():
        if isinstance(exc, cls):
            return HTTPException(status_code=code, detail=detail)
    if isinstance(exc, svc.SiteLogValidationError):
        return HTTPException(status_code=422, detail=exc.message)
    if isinstance(exc, svc.SiteLogNotReady):
        return HTTPException(
            status_code=409,
            detail={"message": "Attachments still in flight", "states": exc.states},
        )
    return HTTPException(status_code=500, detail="Site log error")


@router.post("/site-log-events", response_model=SiteLogEventOut)
async def declare_capture(
    payload: CaptureDeclareIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: EvidenceStorage = Depends(get_evidence_storage),
    session_factory=Depends(get_session_factory),
):
    try:
        result = await svc.declare_capture(
            db, storage, session_factory,
            user=user,
            capture_client_id=payload.capture_client_id,
            job_id=payload.job_id,
            occurred_at=payload.occurred_at,
            internal_location=payload.internal_location,
            body_text=payload.body_text,
            attachments=[a.model_dump() for a in payload.attachments],
            max_bytes=get_settings().evidence_max_upload_bytes,
        )
    except svc.SiteLogError as exc:
        raise _map(exc) from exc
    body = _out(result.view)
    if result.inline_failed:
        # Durable, replayable state: 502 carries the current state.
        raise HTTPException(status_code=502, detail=body.model_dump(mode="json"))
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return body


@router.put(
    "/site-log-events/{event_id}/attachments/{attachment_client_id}",
    response_model=UploadOut,
)
async def upload_attachment(
    event_id: uuid.UUID,
    attachment_client_id: uuid.UUID,
    file: UploadFile,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: EvidenceStorage = Depends(get_evidence_storage),
    session_factory=Depends(get_session_factory),
):
    try:
        result = await svc.upload_attachment(
            db, storage, session_factory,
            user=user, event_id=event_id,
            attachment_client_id=attachment_client_id,
            mime_type=file.content_type or "application/octet-stream",
            chunks=evidence_service.upload_chunks_from(file),
            max_bytes=get_settings().evidence_max_upload_bytes,
        )
    except svc.SiteLogError as exc:
        raise _map(exc) from exc
    except EvidenceStorageError as exc:
        raise HTTPException(status_code=502, detail="Evidence storage backend error") from exc
    response.status_code = status.HTTP_200_OK if result.replay else status.HTTP_201_CREATED
    return UploadOut(
        attachment_client_id=result.attachment.attachment_client_id,
        state=result.attachment.state,
        evidence_id=result.evidence.evidence_id,
        sha256=result.evidence.sha256,
        size_bytes=result.evidence.size_bytes,
    )


@router.post(
    "/site-log-events/{event_id}/attachments/{attachment_client_id}/reset",
    response_model=AttachmentOut,
)
async def reset_attachment(
    event_id: uuid.UUID,
    attachment_client_id: uuid.UUID,
    payload: ResetAttachmentIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        att = await svc.reset_attachment(
            db, admin=user, event_id=event_id,
            attachment_client_id=attachment_client_id,
            reason=payload.reason, now=datetime.now(UTC),
        )
    except svc.SiteLogError as exc:
        raise _map(exc) from exc
    return AttachmentOut.model_validate(att)


@router.post("/site-log-events/{event_id}/finalize", response_model=SiteLogEventOut)
async def finalize_capture(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _out(await svc.finalize_capture(db, user=user, event_id=event_id))
    except svc.SiteLogError as exc:
        raise _map(exc) from exc


@router.post("/site-log-events/{event_id}/assign-job", response_model=SiteLogEventOut)
async def assign_job(
    event_id: uuid.UUID,
    payload: AssignJobIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _out(await svc.assign_job(db, user=user, event_id=event_id, job_id=payload.job_id))
    except svc.SiteLogError as exc:
        raise _map(exc) from exc


@router.post("/site-log-events/{event_id}/relink-job", response_model=SiteLogEventOut)
async def relink_job(
    event_id: uuid.UUID,
    payload: RelinkJobIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _out(await svc.relink_job(
            db, user=user, event_id=event_id, job_id=payload.job_id, reason=payload.reason,
        ))
    except svc.SiteLogError as exc:
        raise _map(exc) from exc


@router.get("/site-log-events/unassigned", response_model=list[SiteLogEventOut])
async def list_unassigned(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return [_out(v) for v in await svc.list_unassigned(db, user)]


@router.get("/site-log-events/{event_id}", response_model=SiteLogEventOut)
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return _out(await svc.get_event(db, user, event_id))
    except svc.SiteLogError as exc:
        raise _map(exc) from exc


@router.get("/jobs/{job_id}/site-log-events", response_model=list[SiteLogEventOut])
async def list_job_events(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return [_out(v) for v in await svc.list_job_events(db, user, job_id)]
    except svc.SiteLogError as exc:
        raise _map(exc) from exc
