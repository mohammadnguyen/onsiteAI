"""Evidence endpoints (raw-evidence layer, DEC-EVIDENCE-001).

Routes (absolute paths — the router is included unprefixed because the
job-scoped listing lives under ``/jobs``, same pattern as labour):

* ``POST /evidence`` — multipart streaming upload.
* ``GET /evidence/{id}`` — metadata.
* ``GET /evidence/{id}/download`` — byte stream, always
  ``Content-Disposition: attachment``.
* ``POST /evidence/{id}/link-job`` — explicit user action linking the
  authoritative Job (DEC-JOB-ATTR-001).
* ``GET /jobs/{job_id}/evidence`` — evidence linked to one job.

No delete route exists, deliberately (DEC-EVIDENCE-001).
"""

import mimetypes
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.evidence import EvidenceLinkJobIn, EvidenceOut
from app.services import evidence as evidence_service
from app.services.evidence_storage import (
    EvidenceStorage,
    EvidenceStorageError,
    get_evidence_storage,
)

router = APIRouter(tags=["evidence"])


@router.post(
    "/evidence",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_evidence(
    file: UploadFile,
    occurred_at: datetime | None = Form(
        default=None,
        description=(
            "When the evidence was captured on site (DEC-TIME-001); "
            "distinct from the record's created_at. Optional at the raw "
            "layer: when absent it stays NULL — the server never "
            "defaults it to upload time or any other value."
        ),
    ),
    job_id: uuid.UUID | None = Form(
        default=None,
        description=(
            "EXPLICIT user-selected Job only (DEC-JOB-ATTR-001). Clients "
            "must never pass a GPS/AI-suggested value here; suggestions "
            "are confirmed by the user before they become this field."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: EvidenceStorage = Depends(get_evidence_storage),
):
    settings = get_settings()
    try:
        evidence = await evidence_service.create_evidence(
            db,
            storage,
            uploader=user,
            chunks=evidence_service.upload_chunks_from(file),
            mime_type=file.content_type or "application/octet-stream",
            occurred_at=occurred_at,
            original_filename=file.filename,
            job_id=job_id,
            max_bytes=settings.evidence_max_upload_bytes,
        )
    except evidence_service.EvidenceJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        ) from exc
    except evidence_service.EvidenceTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Upload exceeds the evidence size limit of "
                f"{settings.evidence_max_upload_bytes} bytes"
            ),
        ) from exc
    except EvidenceStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Evidence storage backend error",
        ) from exc
    return evidence


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
async def get_evidence(
    evidence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await evidence_service.get_evidence(db, user, evidence_id)
    except evidence_service.EvidenceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence not found",
        ) from exc


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(
    evidence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: EvidenceStorage = Depends(get_evidence_storage),
):
    try:
        evidence, stream = await evidence_service.open_evidence_stream(
            db, storage, user, evidence_id
        )
    except evidence_service.EvidenceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence not found",
        ) from exc
    except evidence_service.EvidenceNotStored as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Evidence is {exc.status.value}, no stored bytes",
        ) from exc

    # Server-generated safe filename: evidence_id + extension derived
    # from the stored MIME type. The client-supplied original_filename
    # never reaches this header (it stays JSON metadata only), so
    # hostile names (quotes, CRLF, unicode) cannot inject into it.
    ext = mimetypes.guess_extension(evidence.mime_type) or ""
    filename = f"{evidence.evidence_id}{ext}"
    # Always attachment — evidence is downloaded, never rendered inline
    # from the API origin (founder ruling; also XSS hygiene for
    # user-supplied content types).
    return StreamingResponse(
        stream,
        media_type=evidence.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **(
                {"Content-Length": str(evidence.size_bytes)}
                if evidence.size_bytes is not None
                else {}
            ),
        },
    )


@router.post("/evidence/{evidence_id}/link-job", response_model=EvidenceOut)
async def link_evidence_job(
    evidence_id: uuid.UUID,
    payload: EvidenceLinkJobIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Initial link (any reader) or admin-only relink with reason.

    Relink is job→job only and always audited (``job_relinked`` with
    old/new/reason); evidence bytes and storage keys are untouched.
    """
    try:
        return await evidence_service.link_job(
            db, user, evidence_id, payload.job_id, reason=payload.reason
        )
    except evidence_service.EvidenceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence not found",
        ) from exc
    except evidence_service.EvidenceBoundToEvent as exc:
        # WP A A2a: bound Evidence follows its Site Log event's Job.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Evidence is attached to a site log event; change the "
                "job via the event's assign-job / relink-job actions"
            ),
        ) from exc
    except evidence_service.EvidenceJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        ) from exc
    except evidence_service.EvidenceRelinkForbidden as exc:
        # Repo's admin-only convention: 403 "Admin only", matching
        # app.deps.require_admin.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin only"
        ) from exc
    except evidence_service.EvidenceRelinkReasonRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Relink requires a non-empty reason",
        ) from exc
    except evidence_service.EvidenceRelinkSameJob as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence is already linked to this job",
        ) from exc


@router.get("/jobs/{job_id}/evidence", response_model=list[EvidenceOut])
async def list_job_evidence(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await evidence_service.list_job_evidence(db, user, job_id)
    except evidence_service.EvidenceJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        ) from exc
