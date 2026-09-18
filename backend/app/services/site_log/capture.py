"""Declaring a capture, and the server-owned inline-text upload.

The orchestrating cluster: it reaches into :mod:`upload` to store the inline
bytes and into :mod:`admin` to finalize. Nothing imports it back.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site_log import (
    AttachmentState,
    CaptureEligibilityState,
    CaptureEligibilityTransition,
    CaptureStatus,
    SiteLogAuditAction,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventRevision,
)
from app.models.user import User
from app.services.evidence_storage import EvidenceStorage, EvidenceStorageError
from app.services.site_log_access import TENANT_ID

from .admin import finalize_capture
from .core import INLINE_TEXT_MIME, SessionFactory, _audit, _is_nonempty_text, logger
from .errors import (
    SiteLogContentMismatch,
    SiteLogFingerprintMismatch,
    SiteLogNotReady,
    SiteLogTooLarge,
    SiteLogUploadInProgress,
    SiteLogValidationError,
)
from .inline import _inline_expectation, declaration_fingerprint, inline_attachment_id
from .upload import upload_attachment
from .views import EventView, _fingerprint_of, _target_job, _view


@dataclass
class DeclareResult:
    view: EventView
    created: bool
    inline_failed: bool = False


async def declare_capture(
    db: AsyncSession,
    storage: EvidenceStorage,
    session_factory: SessionFactory,
    *,
    user: User,
    capture_client_id: uuid.UUID,
    job_id: uuid.UUID | None,
    occurred_at: datetime | None,
    internal_location: str | None,
    body_text: str | None,
    attachments: list[dict],
    max_bytes: int,
) -> DeclareResult:
    """Phase 1 of the two-phase protocol (2.1 §3, all four shapes).

    One transaction creates event + revision 1 + manifest rows + initial
    eligibility transition + content-free ``created`` audit carrying the
    declaration fingerprint. Inline text (shapes 1–2) is then uploaded
    server-side in this same HTTP request via the normal attachment path
    (short transactions around storage; never a transaction across IO).
    """
    if body_text is not None and not _is_nonempty_text(body_text):
        raise SiteLogValidationError("body_text must contain non-whitespace text")
    has_text = _is_nonempty_text(body_text)
    if not has_text and not attachments:
        raise SiteLogValidationError("a capture needs body_text or attachments")
    inline_id = inline_attachment_id(capture_client_id)
    seen: set[uuid.UUID] = set()
    for a in attachments:
        cid = a["attachment_client_id"]
        if cid == inline_id:
            raise SiteLogValidationError(
                "attachment_client_id collides with the reserved inline-text id"
            )
        if cid in seen:
            raise SiteLogValidationError("duplicate attachment_client_id")
        seen.add(cid)
        if a.get("declared_size_bytes") is not None and a["declared_size_bytes"] < 0:
            raise SiteLogValidationError("declared_size_bytes must be >= 0")

    fingerprint = declaration_fingerprint(
        body_text=body_text,
        internal_location=internal_location,
        occurred_at=occurred_at,
        job_id=job_id,
        attachments=attachments,
    )

    if job_id is not None:
        await _target_job(db, job_id, user)

    event = SiteLogEvent(
        site_log_event_id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        author_user_id=user.user_id,
        job_id=job_id,
        capture_client_id=capture_client_id,
        capture_status=CaptureStatus.pending_upload,
    )
    created = True
    try:
        # SAVEPOINT: a unique-key miss rolls back only the failed insert,
        # leaving the caller's loaded objects (user, job) unexpired.
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        created = False
        q = select(SiteLogEvent).where(
            SiteLogEvent.tenant_id == TENANT_ID,
            SiteLogEvent.author_user_id == user.user_id,
            SiteLogEvent.capture_client_id == capture_client_id,
        )
        event = (await db.execute(q)).scalar_one()
        if await _fingerprint_of(db, event) != fingerprint:
            raise SiteLogFingerprintMismatch() from None

    if created:
        db.add(
            SiteLogEventRevision(
                tenant_id=event.tenant_id,
                site_log_event_id=event.site_log_event_id,
                revision_no=1,
                body_text=body_text if has_text else None,
                internal_location=internal_location,
                occurred_at=occurred_at,
                withdrawn=False,
                reason=None,
                actor_user_id=user.user_id,
            )
        )
        rows = list(attachments)
        if has_text:
            rows.append(
                {
                    "attachment_client_id": inline_id,
                    "declared_media_type": "text",
                    "declared_size_bytes": len(body_text.encode("utf-8")),
                }
            )
        for a in rows:
            db.add(
                SiteLogEventAttachment(
                    tenant_id=event.tenant_id,
                    site_log_event_id=event.site_log_event_id,
                    attachment_client_id=a["attachment_client_id"],
                    declared_media_type=a["declared_media_type"],
                    declared_size_bytes=a.get("declared_size_bytes"),
                    state=AttachmentState.awaiting_upload,
                    upload_attempt_no=0,
                )
            )
        db.add(
            CaptureEligibilityTransition(
                tenant_id=event.tenant_id,
                site_log_event_id=event.site_log_event_id,
                transition_no=1,
                from_state=None,
                to_state=CaptureEligibilityState.eligibility_pending_unexposed,
                reason="capture_created",
                actor_user_id=user.user_id,
            )
        )
        db.add(
            _audit(
                event.site_log_event_id,
                event.tenant_id,
                user.user_id,
                SiteLogAuditAction.created,
                {
                    "declaration_fingerprint": fingerprint,
                    "declared_attachment_count": len(rows),
                    "inline_text": has_text,
                    "job_id": str(job_id) if job_id else None,
                },
            )
        )
        await db.commit()
        logger.info(
            "site_log declare event_id=%s attachments=%d inline=%s",
            event.site_log_event_id,
            len(rows),
            has_text,
        )

    inline_failed = False
    if has_text:
        inline_failed = await _run_inline_text(
            db,
            storage,
            session_factory,
            user=user,
            event=event,
            inline_id=inline_id,
            max_bytes=max_bytes,
        )
        if not inline_failed and not attachments:
            # Shape 1: server-finalizes in the same request. NotReady only
            # when the inline row was left pending by a prior process death.
            with contextlib.suppress(SiteLogNotReady):
                await finalize_capture(db, user=user, event_id=event.site_log_event_id)
    view = await _view(db, event)
    return DeclareResult(view=view, created=created, inline_failed=inline_failed)


async def _run_inline_text(
    db: AsyncSession,
    storage: EvidenceStorage,
    session_factory: SessionFactory,
    *,
    user: User,
    event: SiteLogEvent,
    inline_id: uuid.UUID,
    max_bytes: int,
) -> bool:
    """Upload the inline row if it is awaiting/failed; leave pending alone
    (2.1 §1). Returns True when the upload was attempted and failed.

    The bytes come from revision 1 in the database, never from the request
    that triggered this call. A declaration replay therefore re-uploads the
    words the event was captured with, even if the caller sent different
    ones — and a caller who sends different ones is already refused by the
    declaration fingerprint before reaching here.
    """
    expectation = await _inline_expectation(db, event)
    if expectation is None:
        # Revision 1 is missing or carries no text. There is nothing
        # authoritative to upload, and the request body is not a substitute
        # for it, so the attempt is refused rather than guessed at.
        logger.error(
            "site_log inline upload refused: revision 1 unreadable event_id=%s",
            event.site_log_event_id,
        )
        return True

    async def _one_chunk():
        yield expectation.body_text.encode("utf-8")  # exact bytes, no normalisation

    try:
        await upload_attachment(
            db,
            storage,
            session_factory,
            user=user,
            event_id=event.site_log_event_id,
            attachment_client_id=inline_id,
            mime_type=INLINE_TEXT_MIME,
            chunks=_one_chunk(),
            max_bytes=max_bytes,
            internal=True,
        )
        return False
    except SiteLogUploadInProgress:
        return False  # pending@N — never touched by a replay
    except (EvidenceStorageError, SiteLogTooLarge, SiteLogContentMismatch):
        return True
