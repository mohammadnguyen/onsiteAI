"""The two-phase upload: Txn A, the byte stream, Txn B, and failure.

Deliberately one module. ``upload_attachment`` reaches ``acquire_attachment``,
``complete_attachment``, ``_fail_attachment`` and ``_fail_upload_attempt``
through this module's own globals, and the fault-injection tests patch them
there; splitting the caller from its callees would leave those patches
pointing at names nobody looks up.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence, EvidenceStatus
from app.models.job import Job
from app.models.site_log import (
    ATTACHMENT_STATE_TRANSITIONS,
    AttachmentState,
    SiteLogAuditAction,
    SiteLogEventAttachment,
)
from app.models.user import User, UserRole
from app.services.evidence import derive_media_type
from app.services.evidence_storage import (
    EvidenceStorage,
    EvidenceStorageError,
    ObjectAlreadyExists,
    StoredObject,
)
from app.services.site_log_access import can_read_event

from .core import (
    TXN_B_ATTEMPTS,
    TXN_B_BACKOFF_SECONDS,
    SessionFactory,
    _audit,
    _capped,
    _evidence_audit,
    _is_retryable,
    _lock_attachment,
    _lock_attachments,
    _lock_event,
    _lock_evidence_rows,
    _lock_scope,
    logger,
)
from .errors import (
    SiteLogAttemptSuperseded,
    SiteLogContentMismatch,
    SiteLogInlineReserved,
    SiteLogMediaMismatch,
    SiteLogNotFound,
    SiteLogTooLarge,
    SiteLogUploadInProgress,
)
from .inline import _inline_expectation, inline_attachment_id


@dataclass
class UploadResult:
    attachment: SiteLogEventAttachment
    evidence: Evidence
    replay: bool


async def acquire_attachment(
    db: AsyncSession,
    *,
    user: User,
    event_id: uuid.UUID,
    attachment_client_id: uuid.UUID,
    mime_type: str,
    internal: bool = False,
) -> tuple[SiteLogEventAttachment, Evidence, int, bool]:
    """Upload Txn A (2.1 §2 rows 1 and 9). Commits.

    Returns ``(attachment, evidence, attempt_no, replay)``. ``replay`` is
    True when the row is already ``stored`` — nothing is written.

    ``internal`` is set only by the server's own inline-text path. Every
    route into this function from the API leaves it False, which is what
    reserves the inline row.
    """
    async with _lock_scope(db) as sp:
        event = await _lock_event(db, event_id)
        job = None if event is None or event.job_id is None else await db.get(Job, event.job_id)
        if event is None or not can_read_event(user, event, job):
            raise SiteLogNotFound()
        if user.role != UserRole.admin and event.author_user_id != user.user_id:
            raise SiteLogNotFound()  # only author/admin upload; existence hidden
        att = await _lock_attachment(db, event, attachment_client_id)
        if att is None:
            raise SiteLogNotFound()
        if not internal and attachment_client_id == inline_attachment_id(
            event.capture_client_id
        ):
            # The inline row is server-owned. Refused AFTER the visibility
            # and author/admin checks above, so a caller who cannot see the
            # event still learns only that it does not exist; and refused
            # BEFORE any branch that writes, so a refusal changes no state
            # and records no audit. Ordinary rows are untouched by this.
            raise SiteLogInlineReserved()
        if att.state is AttachmentState.stored:
            evidence = await db.get(Evidence, att.evidence_id)
            await sp.rollback()  # no-write replay: release locks only
            return att, evidence, att.upload_attempt_no, True
        if att.state is AttachmentState.pending:
            raise SiteLogUploadInProgress()
        if derive_media_type(mime_type).value != att.declared_media_type:
            raise SiteLogMediaMismatch()  # rejected before acquisition

        assert AttachmentState.pending in ATTACHMENT_STATE_TRANSITIONS[att.state]
        prev_state = att.state
        new_attempt = att.upload_attempt_no + 1

        if att.evidence_id is None:
            # The ONLY NULL→value write of evidence_id in the codebase, and
            # the only Evidence creation site on the Site Log path.
            evidence = Evidence(
                evidence_id=uuid.uuid4(),
                job_id=event.job_id,
                uploaded_by_user_id=user.user_id,
                media_type=derive_media_type(mime_type),
                mime_type=mime_type,
                original_filename=None,
                status=EvidenceStatus.pending,
                occurred_at=None,
            )
            db.add(evidence)
            await db.flush()
            att.evidence_id = evidence.evidence_id
            db.add(
                _evidence_audit(
                    evidence.evidence_id,
                    user.user_id,
                    "uploaded",
                    {"mime_type": mime_type, "attempt_no": new_attempt,
                     "site_log_event_id": str(event.site_log_event_id)},
                )
            )
        else:
            rows = await _lock_evidence_rows(db, [att.evidence_id])
            evidence = rows[0]
            assert evidence.status is not EvidenceStatus.stored
            evidence.status = EvidenceStatus.pending  # failed → pending (row 9)
            db.add(
                _evidence_audit(
                    evidence.evidence_id,
                    user.user_id,
                    "uploaded",
                    {"attempt_no": new_attempt, "retry": True},
                )
            )

        att.state = AttachmentState.pending
        att.upload_attempt_no = new_attempt
        db.add(
            _audit(
                event.site_log_event_id,
                event.tenant_id,
                user.user_id,
                SiteLogAuditAction.attachment_state_changed,
                {
                    "attachment_client_id": str(attachment_client_id),
                    "from": prev_state.value,
                    "to": AttachmentState.pending.value,
                    "attempt_no": new_attempt,
                },
            )
        )
    await db.commit()  # Txn A: governed row is durable before any byte
    return att, evidence, new_attempt, False


async def _fail_attachment(
    db: AsyncSession,
    *,
    actor: User,
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    attempt_no: int,
    reason: str,
    detail: dict | None = None,
) -> None:
    """Rows 4/5: manifest pending→failed and Evidence pending→failed,
    atomically, under the global lock order. Commits on write; a
    superseded or vanished row is a no-write return.

    ``detail`` merges extra content-free keys (e.g. the exception class of
    an unexpected failure) into both audit payloads; omitted, the payloads
    are byte-identical to every earlier caller's."""
    async with _lock_scope(db) as sp:
        event = await _lock_event(db, event_id)
        if event is None:
            await sp.rollback()
            return
        atts = [
            a for a in await _lock_attachments(db, event) if a.attachment_id == attachment_id
        ]
        if not atts:
            await sp.rollback()
            return
        att = atts[0]
        if att.state is not AttachmentState.pending or att.upload_attempt_no != attempt_no:
            await sp.rollback()  # superseded; nothing to fail
            return
        ev_rows = await _lock_evidence_rows(db, [att.evidence_id])
        att.state = AttachmentState.failed
        for ev in ev_rows:
            ev.status = EvidenceStatus.failed
            db.add(
                _evidence_audit(
                    ev.evidence_id, actor.user_id, "failed",
                    {"reason": reason, "attempt_no": attempt_no, **(detail or {})},
                )
            )
        db.add(
            _audit(
                event.site_log_event_id, event.tenant_id, actor.user_id,
                SiteLogAuditAction.attachment_state_changed,
                {"attachment_client_id": str(att.attachment_client_id),
                 "from": "pending", "to": "failed",
                 "attempt_no": attempt_no, "reason": reason,
                 **(detail or {})},
            )
        )
    await db.commit()


async def _complete_once(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    attempt_no: int,
    stored: StoredObject,
    backend_name: str,
) -> SiteLogEventAttachment:
    """One Txn B attempt (row 2). Lock order event → manifest → Evidence;
    the CAS is the locked check ``pending ∧ attempt_no``."""
    event = await _lock_event(db, event_id)
    if event is None:
        raise SiteLogNotFound()
    atts = [a for a in await _lock_attachments(db, event) if a.attachment_id == attachment_id]
    if not atts:
        raise SiteLogNotFound()
    att = atts[0]
    if att.state is not AttachmentState.pending or att.upload_attempt_no != attempt_no:
        raise SiteLogAttemptSuperseded()
    ev_rows = await _lock_evidence_rows(db, [att.evidence_id])
    evidence = ev_rows[0]
    if att.attachment_client_id == inline_attachment_id(event.capture_client_id):
        # Past the CAS, so this is the winning attempt and an obsolete one
        # can never fail it. Before any write, so a mismatch binds nothing.
        # Covers every route that produces a receipt, the adoption branch
        # included: what is checked is the receipt, not how it was obtained.
        expectation = await _inline_expectation(db, event)
        if (
            expectation is None
            or stored.sha256 != expectation.sha256
            or stored.size_bytes != expectation.size_bytes
        ):
            att.state = AttachmentState.failed
            evidence.status = EvidenceStatus.failed
            # Content-free on purpose: neither the stored bytes, their hash
            # nor their length is recorded, so the audit cannot become a
            # copy of the text it exists to protect.
            db.add(
                _evidence_audit(
                    evidence.evidence_id, actor_id, "failed",
                    {"reason": "content_mismatch", "attempt_no": attempt_no},
                )
            )
            db.add(
                _audit(
                    event.site_log_event_id, event.tenant_id, actor_id,
                    SiteLogAuditAction.attachment_state_changed,
                    {"attachment_client_id": str(att.attachment_client_id),
                     "from": "pending", "to": "failed",
                     "attempt_no": attempt_no, "reason": "content_mismatch"},
                )
            )
            await db.commit()  # the failure and its audit land together
            raise SiteLogContentMismatch()
    # Value columns written exactly once, here, for the winning attempt.
    evidence.status = EvidenceStatus.stored
    evidence.size_bytes = stored.size_bytes
    evidence.sha256 = stored.sha256
    evidence.storage_backend = backend_name
    evidence.storage_key = stored.key
    evidence.job_id = event.job_id  # read under the event lock
    att.state = AttachmentState.stored
    db.add(
        _evidence_audit(
            evidence.evidence_id, actor_id, "stored",
            {"size_bytes": stored.size_bytes, "sha256": stored.sha256,
             "attempt_no": attempt_no},
        )
    )
    db.add(
        _audit(
            event.site_log_event_id, event.tenant_id, actor_id,
            SiteLogAuditAction.attachment_state_changed,
            {"attachment_client_id": str(att.attachment_client_id),
             "from": "pending", "to": "stored", "attempt_no": attempt_no,
             "size_bytes": stored.size_bytes, "sha256": stored.sha256},
        )
    )
    await db.commit()
    return att


async def complete_attachment(
    session_factory: SessionFactory,
    *,
    actor_id: uuid.UUID,
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    attempt_no: int,
    stored: StoredObject,
    backend_name: str,
) -> SiteLogEventAttachment:
    """Txn B with the bounded internal retry: fresh session per attempt,
    same in-memory attempt number and verified storage result."""
    last: BaseException | None = None
    for i in range(TXN_B_ATTEMPTS):
        session = session_factory()
        try:
            return await _complete_once(
                session,
                actor_id=actor_id,
                event_id=event_id,
                attachment_id=attachment_id,
                attempt_no=attempt_no,
                stored=stored,
                backend_name=backend_name,
            )
        except (SiteLogAttemptSuperseded, SiteLogNotFound):
            await session.rollback()
            raise
        except DBAPIError as exc:
            await session.rollback()
            if not _is_retryable(exc) or i == TXN_B_ATTEMPTS - 1:
                raise
            last = exc
            logger.warning(
                "site_log txn_b retry attachment_id=%s attempt_no=%d try=%d",
                attachment_id, attempt_no, i + 1,
            )
            await asyncio.sleep(TXN_B_BACKOFF_SECONDS[i])
        finally:
            await session.close()
    raise last  # pragma: no cover — loop always returns or raises


async def upload_attachment(
    db: AsyncSession,
    storage: EvidenceStorage,
    session_factory: SessionFactory,
    *,
    user: User,
    event_id: uuid.UUID,
    attachment_client_id: uuid.UUID,
    mime_type: str,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    internal: bool = False,
) -> UploadResult:
    """Phase 2 orchestration: Txn A → stream (no lock) → Txn B / fail."""
    att, evidence, attempt_no, replay = await acquire_attachment(
        db, user=user, event_id=event_id,
        attachment_client_id=attachment_client_id, mime_type=mime_type,
        internal=internal,
    )
    if replay:
        return UploadResult(attachment=att, evidence=evidence, replay=True)

    attachment_id = att.attachment_id
    evidence_id = evidence.evidence_id
    # UPLOAD PHASE ONLY. Everything after this block (Txn B, the refreshes)
    # is outside it on purpose: a completion or commit failure, and a row
    # whose bytes are already stored, must never be recorded as an upload
    # failure. ``asyncio.CancelledError`` is a BaseException and is not
    # caught anywhere here — a cancelled or killed attempt keeps the
    # existing recovery rule (row stays pending, admin reset ≥ 15 min).
    try:
        stored = await _stream_to_storage(
            storage,
            evidence_id=evidence_id,
            chunks=chunks,
            max_bytes=max_bytes,
            attempt_no=attempt_no,
        )
    except SiteLogTooLarge:
        # Established handling, unchanged: a failure of the bookkeeping
        # write itself propagates here rather than being swallowed, so the
        # client is never told 413 over a row that stayed pending.
        await _fail_attachment(
            db, actor=user, event_id=event_id, attachment_id=attachment_id,
            attempt_no=attempt_no, reason="size_cap",
        )
        raise
    except EvidenceStorageError:
        await _fail_attachment(
            db, actor=user, event_id=event_id, attachment_id=attachment_id,
            attempt_no=attempt_no, reason="storage_error",
        )
        raise
    except Exception as exc:
        # Anything else the upload phase raised — in practice an exception
        # from the chunk SOURCE (reading the spooled request body), which
        # the storage adapter re-raises unchanged (WP-S(4)). Record the
        # attempt as failed, then let the ORIGINAL exception propagate: it
        # stays unhandled at the API (500), exactly as before this change.
        # ``internal_error`` is the A2a.2 code for unexpected non-adapter
        # failures (design B6/B11), so no new reason string is introduced.
        logger.error(
            "site_log upload source failure event_id=%s attachment_id=%s "
            "attempt_no=%d error=%s",
            event_id, attachment_id, attempt_no, type(exc).__name__,
        )
        await _fail_upload_attempt(
            db, actor=user, event_id=event_id, attachment_id=attachment_id,
            attempt_no=attempt_no, error=exc,
        )
        raise

    await complete_attachment(
        session_factory,
        actor_id=user.user_id,
        event_id=event_id,
        attachment_id=attachment_id,
        attempt_no=attempt_no,
        stored=stored,
        backend_name=storage.backend_name,
    )
    # Txn B ran on another session: reload exactly the two rows it wrote
    # into the caller's session (no blanket expiry — the caller's user and
    # event objects stay readable outside a greenlet context).
    await db.refresh(att)
    await db.refresh(evidence)
    return UploadResult(attachment=att, evidence=evidence, replay=False)


async def _stream_to_storage(
    storage: EvidenceStorage,
    *,
    evidence_id: uuid.UUID,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    attempt_no: int,
) -> StoredObject:
    """The upload phase of one attempt, as ONE failure boundary.

    Streams this attempt's bytes and, on a collision at this attempt's own
    key, adopts the existing object. The adoption reads (``exists`` /
    ``open``) are inside the boundary on purpose: since WP-S(1) they raise
    classified errors instead of answering ``False`` / ``ObjectNotFound``,
    and every one of those failures must reach the caller's failed-attempt
    bookkeeping rather than escape from inside an exception handler.

    Under attempt-scoped final keys (WP-S-core, FD1) an
    ``ObjectAlreadyExists`` here is no longer the identical-bytes retry
    this branch was written for — it can only be a re-put of the same
    ``(evidence_id, attempt_no)``, i.e. an anomaly after a database restore
    rewound the attempt counter. A2a.2 replaces the branch with an explicit
    held failure (design A5 / B6); it is kept as-is in this slice.
    """
    try:
        return await storage.put(
            str(evidence_id), _capped(chunks, max_bytes), attempt_no=attempt_no
        )
    except ObjectAlreadyExists as exc:
        key = str(exc)
        if not await storage.exists(key):
            raise EvidenceStorageError("collision without object") from exc
        return await _adopt(storage, key)


async def _fail_upload_attempt(
    db: AsyncSession,
    *,
    actor: User,
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    attempt_no: int,
    error: BaseException,
) -> None:
    """Record the ``internal_error`` failed transition for the attempt whose
    upload phase raised ``error``; the caller then re-raises the SAME
    exception object (type and identity preserved — a diagnostic note may be
    attached to it on the bookkeeping-failure path below).

    The write itself is :func:`_fail_attachment` — reused, not reimplemented,
    so the attempt check, the global lock order and the one-commit atomicity
    of manifest + Evidence + both audit rows are exactly the ones already
    pinned by tests. A late failure from a superseded attempt is therefore a
    no-write return and cannot touch a newer attempt.

    If the bookkeeping itself fails, its persistence outcome is UNKNOWN to
    this function. The exception can arrive before the write was committed
    (nothing persisted — the attempt is still ``pending`` and recovers
    through the admin reset) or after the database committed it and only the
    acknowledgement was lost (the attempt is already ``failed``). Nothing
    available here distinguishes the two, so the log line and the note
    attached to ``error`` state the uncertainty instead of asserting either
    outcome; the row itself is the evidence. No retry, no reset and no
    further state transition is attempted. Only the ``size_cap`` and
    ``storage_error`` classes keep their established handling, where such a
    bookkeeping failure propagates instead of being attached here.

    Content-free by rule: identifiers, the reason and exception class names.
    """
    try:
        await _fail_attachment(
            db, actor=actor, event_id=event_id, attachment_id=attachment_id,
            attempt_no=attempt_no, reason="internal_error",
            detail={"error_class": type(error).__name__},
        )
    except Exception as bookkeeping_error:
        # Raised before the commit, or after it succeeded and only the
        # acknowledgement was lost — indistinguishable from here.
        note = (
            "site_log failed transition persistence UNCONFIRMED "
            f"(event_id={event_id} attachment_id={attachment_id} "
            f"attempt_no={attempt_no} reason=internal_error): "
            f"{type(bookkeeping_error).__name__}; the attempt is either "
            "failed or still pending — read the row to determine which"
        )
        logger.error("%s upload_error=%s", note, type(error).__name__)
        error.add_note(note)


async def _adopt(storage: EvidenceStorage, key: str) -> StoredObject:
    """Rebuild a StoredObject for an existing identical-content object by
    re-reading it (size + sha verified from bytes, never trusted)."""
    hasher = hashlib.sha256()
    size = 0
    async for chunk in storage.open(key):
        hasher.update(chunk)
        size += len(chunk)
    return StoredObject(key=key, size_bytes=size, sha256=hasher.hexdigest())
