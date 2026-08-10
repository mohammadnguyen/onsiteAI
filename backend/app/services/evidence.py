"""Evidence service — upload, read, and explicit job linking.

Write semantics (DEC-EVIDENCE-001 / DEC-TIME-001 / DEC-JOB-ATTR-001):

* Upload is two transactions: (1) insert the ``pending`` row + audit
  ``uploaded`` so a trace exists even if the process dies mid-stream;
  (2) after the adapter verifies the bytes, flip to ``stored`` (or
  ``failed``) + audit. Abandoned ``pending`` rows are found manually:

      SELECT evidence_id, created_at FROM evidence
      WHERE status = 'pending' AND created_at < now() - interval '1 day';

  (No automated sweep in this slice — deliberate.)
* ``job_id`` is written by exactly two code paths, both explicit user
  actions: the ``job_id`` form field on upload, and :func:`link_job`
  (initial link, or admin-only reasoned relink — see its docstring).
  Every write carries an audit row; nothing else may write it —
  suggestion state belongs to the future capture slice
  (DEC-JOB-ATTR-001).
* ``occurred_at`` may be NULL (unknown) and is never defaulted
  server-side (DEC-TIME-001).
* There is no delete path anywhere in this module.

Read access rule (founder ruling): until job-linked, an evidence record
is readable only by its uploader and admins; once linked, it follows the
V1 single-tenant convention (any active authenticated user, same as
expenses on a job). Denials surface as 404, never 403 (cross-job
semantics precedent).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, User, UserRole
from app.models.evidence import (
    Evidence,
    EvidenceAuditLog,
    EvidenceMediaType,
    EvidenceStatus,
)
from app.services.evidence_storage import (
    CHUNK_SIZE,
    EvidenceStorage,
    EvidenceStorageError,
)

logger = logging.getLogger(__name__)


class EvidenceNotFound(Exception):
    """Evidence missing OR caller may not see it — HTTP layer maps to 404."""

    def __init__(self, evidence_id: uuid.UUID):
        self.evidence_id = evidence_id
        super().__init__(f"evidence {evidence_id} not found")


class EvidenceJobNotFound(Exception):
    """Referenced job missing — HTTP layer maps to 404."""

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        super().__init__(f"job {job_id} not found")


class EvidenceRelinkForbidden(Exception):
    """Relink attempted by a non-admin — HTTP layer maps to 403 (the
    repo's admin-only convention, matching ``deps.require_admin``)."""


class EvidenceRelinkReasonRequired(Exception):
    """Relink without a non-empty reason — HTTP layer maps to 422."""


class EvidenceRelinkSameJob(Exception):
    """Relink to the currently-linked job — nothing to change; 409."""


class EvidenceTooLarge(Exception):
    """Upload exceeded the configured cap — HTTP layer maps to 413."""


class EvidenceNotStored(Exception):
    """Download requested for a record whose bytes never landed — 409."""

    def __init__(self, status: EvidenceStatus):
        self.status = status
        super().__init__(f"evidence is {status.value}, not stored")


def derive_media_type(mime_type: str) -> EvidenceMediaType:
    """Coarse media class from MIME. Unknown types are documents."""
    lowered = mime_type.lower()
    if lowered.startswith("audio/"):
        return EvidenceMediaType.audio
    if lowered.startswith("image/"):
        return EvidenceMediaType.image
    if lowered.startswith("text/"):
        return EvidenceMediaType.text
    return EvidenceMediaType.document


def _can_read(user: User, evidence: Evidence) -> bool:
    if user.role == UserRole.admin:
        return True
    if evidence.uploaded_by_user_id == user.user_id:
        return True
    # Job-linked evidence follows V1 single-tenant read convention.
    return evidence.job_id is not None


def _audit(
    evidence_id: uuid.UUID,
    actor: User,
    action: str,
    detail: dict,
) -> EvidenceAuditLog:
    return EvidenceAuditLog(
        evidence_id=evidence_id,
        actor_user_id=actor.user_id,
        action=action,
        detail=detail,
    )


async def _capped(
    chunks: AsyncIterator[bytes], max_bytes: int
) -> AsyncIterator[bytes]:
    """Re-yield chunks, aborting the stream when the cap is exceeded."""
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise EvidenceTooLarge()
        yield chunk


async def create_evidence(
    db: AsyncSession,
    storage: EvidenceStorage,
    *,
    uploader: User,
    chunks: AsyncIterator[bytes],
    mime_type: str,
    occurred_at: datetime | None,
    original_filename: str | None,
    job_id: uuid.UUID | None,
    max_bytes: int,
) -> Evidence:
    """Stream one evidence object into storage and record it.

    ``job_id``, when provided, is the caller's EXPLICIT selection
    (DEC-JOB-ATTR-001) — the API contract forbids passing a suggested
    or inferred value here.

    ``occurred_at`` is stored exactly as given, including ``None``:
    unknown stays NULL and is NEVER defaulted to upload time or any
    other server-side value — that would conflate the two timestamp
    concepts DEC-TIME-001 exists to separate.
    """
    if job_id is not None:
        job = await db.get(Job, job_id)
        if job is None:
            raise EvidenceJobNotFound(job_id)

    evidence = Evidence(
        evidence_id=uuid.uuid4(),
        job_id=job_id,
        uploaded_by_user_id=uploader.user_id,
        media_type=derive_media_type(mime_type),
        mime_type=mime_type,
        original_filename=original_filename,
        status=EvidenceStatus.pending,
        occurred_at=occurred_at,
    )
    db.add(evidence)
    db.add(
        _audit(
            evidence.evidence_id,
            uploader,
            "uploaded",
            {
                "mime_type": mime_type,
                "job_id": str(job_id) if job_id else None,
                "original_filename": original_filename,
            },
        )
    )
    # Transaction 1: the pending row exists even if streaming dies.
    await db.commit()

    logger.info(
        "evidence upload start evidence_id=%s media=%s backend=%s",
        evidence.evidence_id,
        evidence.media_type.value,
        storage.backend_name,
    )

    try:
        stored = await storage.put(
            str(evidence.evidence_id), _capped(chunks, max_bytes)
        )
    except EvidenceTooLarge:
        evidence.status = EvidenceStatus.failed
        db.add(
            _audit(
                evidence.evidence_id,
                uploader,
                "failed",
                {"reason": "size_cap_exceeded", "max_bytes": max_bytes},
            )
        )
        await db.commit()
        logger.warning(
            "evidence upload failed (size cap) evidence_id=%s",
            evidence.evidence_id,
        )
        raise
    except EvidenceStorageError as exc:
        evidence.status = EvidenceStatus.failed
        db.add(
            _audit(
                evidence.evidence_id,
                uploader,
                "failed",
                {"reason": "storage_error", "error": str(exc)},
            )
        )
        await db.commit()
        logger.error(
            "evidence upload failed (storage) evidence_id=%s error=%s",
            evidence.evidence_id,
            exc,
        )
        raise

    evidence.status = EvidenceStatus.stored
    evidence.size_bytes = stored.size_bytes
    evidence.sha256 = stored.sha256
    evidence.storage_backend = storage.backend_name
    evidence.storage_key = stored.key
    db.add(
        _audit(
            evidence.evidence_id,
            uploader,
            "stored",
            {"size_bytes": stored.size_bytes, "sha256": stored.sha256},
        )
    )
    # Transaction 2: verified facts land atomically with the status flip.
    await db.commit()
    # Load the DB-generated timestamps so response serialization never
    # triggers a lazy refresh outside the async context.
    await db.refresh(evidence)

    logger.info(
        "evidence stored evidence_id=%s size=%d",
        evidence.evidence_id,
        stored.size_bytes,
    )
    return evidence


async def get_evidence(
    db: AsyncSession, user: User, evidence_id: uuid.UUID
) -> Evidence:
    """Fetch one evidence record the caller may read, else 404."""
    evidence = await db.get(Evidence, evidence_id)
    if evidence is None or not _can_read(user, evidence):
        raise EvidenceNotFound(evidence_id)
    return evidence


async def open_evidence_stream(
    db: AsyncSession,
    storage: EvidenceStorage,
    user: User,
    evidence_id: uuid.UUID,
) -> tuple[Evidence, AsyncIterator[bytes]]:
    """Resolve a readable, stored record and open its byte stream."""
    evidence = await get_evidence(db, user, evidence_id)
    if evidence.status != EvidenceStatus.stored or evidence.storage_key is None:
        raise EvidenceNotStored(evidence.status)
    return evidence, storage.open(evidence.storage_key)


async def list_job_evidence(
    db: AsyncSession, user: User, job_id: uuid.UUID
) -> list[Evidence]:
    """All evidence linked to a job, newest first. 404 on unknown job."""
    job = await db.get(Job, job_id)
    if job is None:
        raise EvidenceJobNotFound(job_id)
    result = await db.execute(
        select(Evidence)
        .where(Evidence.job_id == job_id)
        .order_by(Evidence.created_at.desc())
    )
    return list(result.scalars().all())


async def link_job(
    db: AsyncSession,
    user: User,
    evidence_id: uuid.UUID,
    job_id: uuid.UUID,
    reason: str | None = None,
) -> Evidence:
    """Explicit user action linking evidence to its authoritative Job.

    One of exactly two writers of ``evidence.job_id`` (the other is the
    explicit ``job_id`` at upload), and every write lands with an audit
    row — no code path changes ``job_id`` silently.

    * **Initial link** (``job_id`` currently NULL): any user who can
      read the record; audited as ``job_linked``.
    * **Relink** (``job_id`` already set): admin-only in v1, requires a
      non-empty ``reason``; audited as ``job_relinked`` with
      ``old_job_id``, ``new_job_id`` and ``reason`` in detail, so the
      old value is preserved forever. Job→job only — unlinking back to
      NULL is out of scope. Evidence bytes and storage keys stay
      immutable throughout; only this metadata column changes.
    """
    evidence = await get_evidence(db, user, evidence_id)
    job = await db.get(Job, job_id)
    if job is None:
        raise EvidenceJobNotFound(job_id)

    if evidence.job_id is None:
        evidence.job_id = job_id
        db.add(
            _audit(
                evidence.evidence_id,
                user,
                "job_linked",
                {"job_id": str(job_id)},
            )
        )
        action = "job_linked"
    else:
        if user.role != UserRole.admin:
            raise EvidenceRelinkForbidden()
        if reason is None or not reason.strip():
            raise EvidenceRelinkReasonRequired()
        if evidence.job_id == job_id:
            raise EvidenceRelinkSameJob()
        old_job_id = evidence.job_id
        evidence.job_id = job_id
        db.add(
            _audit(
                evidence.evidence_id,
                user,
                "job_relinked",
                {
                    "old_job_id": str(old_job_id),
                    "new_job_id": str(job_id),
                    "reason": reason.strip(),
                },
            )
        )
        action = "job_relinked"

    await db.commit()
    await db.refresh(evidence)
    logger.info(
        "evidence %s evidence_id=%s job_id=%s", action, evidence_id, job_id
    )
    return evidence


async def upload_chunks_from(file, chunk_size: int = CHUNK_SIZE):
    """Adapt a Starlette ``UploadFile`` into fixed-size async chunks.

    Starlette spools multipart bodies to a temp file above a small
    threshold, and this reader never pulls more than ``chunk_size``
    bytes at a time — the full payload is never resident in memory.
    """
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            return
        yield chunk
