"""Site Log capture lifecycle — WP A A2a (Revision 2 + 2.1 addendum).

Sole writer of the five WP A tables (``site_log_events``,
``site_log_event_revisions``, ``site_log_event_attachments``,
``site_log_event_audit_log``, ``capture_eligibility_transitions``) and
the ONLY code that creates, binds or changes the status of Evidence rows
attached to an event. Thinness tests pin those write sites.

Governing rules baked in:

* **Global lock order** for every multi-row write: event → manifest rows
  (``attachment_id`` asc) → Evidence rows (``evidence_id`` asc). No lock
  is ever held across byte streaming.
* **Evidence governed before bytes** (2.1 §2 row 1): upload Txn A creates
  the Evidence row, binds it (the only NULL→value write of
  ``evidence_id``), sets ``pending`` and increments
  ``upload_attempt_no`` — then commits — before a single byte is read.
* **Attempt-versioned completion** (2.1 §1): Txn B completes only when the
  locked manifest row is still ``pending`` AT the acquired attempt number;
  an obsolete attempt can never complete after an admin reset + retry.
* **No time-based self-heal**: a ``pending`` row answers 409 until an
  admin reset (≥ 15 minutes, reason, audited) moves it to ``failed``.
* **Txn B internal retry**: three total attempts on a fresh session each,
  100 ms / 300 ms backoff, only for connection invalidation or SQLSTATE
  40001 / 40P01 / 55P03. Never for IntegrityError, CAS misses or
  validation.
* **Content-free audit**: every audit write passes
  :func:`validate_audit_detail`; nothing in this module logs body text,
  filenames or payload bytes.
* **Tenant**: copied from the locked parent row (or the single-tenant
  constant at event creation), never from client input; every query
  filters on it.

* **Transaction ownership** (founder ruling B): every locked section runs
  in :func:`_lock_scope` — a SAVEPOINT. A denial, conflict, not-found or
  no-write replay rolls back only that SAVEPOINT (locks release, the
  caller's outer transaction and unrelated pending state are untouched);
  only a positive write path commits.

Services raise domain exceptions only — the API layer maps them.

**Binding for this module (founder ruling D, A2a):** this file is
accepted at its current size only because transaction ownership is
concentrated here and heavily tested. No A2b behaviour may be added to
it. Before A2b implementation or any staging deployment, a separate
behaviour-preserving A2a.1 structural-refactor checkpoint must split it
into cohesive modules while retaining ONE public mutation boundary and
the global lock order.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.models.evidence import Evidence, EvidenceAuditLog, EvidenceStatus
from app.models.job import Job, JobStatus
from app.models.site_log import (
    ATTACHMENT_STATE_TRANSITIONS,
    AttachmentState,
    CaptureEligibilityState,
    CaptureEligibilityTransition,
    CaptureStatus,
    SiteLogAuditAction,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
    SiteLogEventRevision,
    validate_audit_detail,
)
from app.models.user import User, UserRole
from app.services.evidence import derive_media_type
from app.services.evidence_storage import (
    EvidenceStorage,
    EvidenceStorageError,
    ObjectAlreadyExists,
    StoredObject,
)
from app.services.site_log_access import (
    TENANT_ID,
    can_read_event,
    can_read_job,
)

logger = logging.getLogger(__name__)

# Deterministic identity of the server-created inline-text manifest row
# (2.1 §3): uuid5(INLINE_TEXT_NAMESPACE, str(capture_client_id)). Pinned
# by a test — changing it is a compatibility change, not a refactor.
INLINE_TEXT_NAMESPACE = uuid.UUID("3f2c1a4e-7b6d-4e0f-9a8c-5d1e2f3a4b6c")
INLINE_TEXT_MIME = "text/plain; charset=utf-8"

RESET_MIN_AGE = timedelta(minutes=15)

# Txn B internal retry (2.1 §1 + implementation ruling).
TXN_B_ATTEMPTS = 3
TXN_B_BACKOFF_SECONDS = (0.1, 0.3)
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})

SessionFactory = Callable[[], AsyncSession]


# ------------------------------------------------------------ exceptions


class SiteLogError(Exception):
    """Base for every Site Log domain error."""


class SiteLogNotFound(SiteLogError):
    """Event/attachment missing, cross-tenant, or not readable — 404."""


class SiteLogJobNotFound(SiteLogError):
    pass


class SiteLogJobCompleted(SiteLogError):
    """Target Job is completed; no new capture/assignment into it — 422."""


class SiteLogValidationError(SiteLogError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SiteLogFingerprintMismatch(SiteLogError):
    """capture_client_id replayed with a different declaration — 409."""


class SiteLogUploadInProgress(SiteLogError):
    """Manifest row is pending; only an admin reset can move it — 409."""


class SiteLogAttemptSuperseded(SiteLogError):
    """Txn B for an obsolete attempt (CAS miss) — 409."""


class SiteLogMediaMismatch(SiteLogError):
    """Actual MIME class disagrees with the declared media type — 422."""


class SiteLogTooLarge(SiteLogError):
    pass


class SiteLogNotReady(SiteLogError):
    def __init__(self, states: dict[str, str]):
        super().__init__("attachments still in flight")
        self.states = states


class SiteLogResetNotEligible(SiteLogError):
    """Pending attempt younger than RESET_MIN_AGE — 409."""


class SiteLogNothingToReset(SiteLogError):
    """Manifest row is not pending — 409."""


class SiteLogForbidden(SiteLogError):
    """Admin-only action attempted by a non-admin — 403 (repo convention)."""


class SiteLogReasonRequired(SiteLogError):
    pass


class SiteLogSameJob(SiteLogError):
    pass


class SiteLogAlreadyAssigned(SiteLogError):
    """assign-job on an event that already has a Job — use relink — 409."""


# ------------------------------------------------------------- helpers


def inline_attachment_id(capture_client_id: uuid.UUID) -> uuid.UUID:
    """Server-derived identity of the inline-text manifest row."""
    return uuid.uuid5(INLINE_TEXT_NAMESPACE, str(capture_client_id))


def declaration_fingerprint(
    *,
    body_text: str | None,
    internal_location: str | None,
    occurred_at: datetime | None,
    job_id: uuid.UUID | None,
    attachments: list[dict],
) -> str:
    """Canonical hash of every creation-time semantic field (2.1 §3).

    Idempotency-key-misuse detection only — never capture identity, never
    deduplication. The inline-text row is covered by ``body_text``, so
    ``attachments`` carries client-declared entries only.
    """
    canonical = {
        "attachments": sorted(
            (
                {
                    "attachment_client_id": str(a["attachment_client_id"]),
                    "declared_media_type": a["declared_media_type"],
                    "declared_size_bytes": a.get("declared_size_bytes"),
                }
                for a in attachments
            ),
            key=lambda a: a["attachment_client_id"],
        ),
        "body_text": body_text,
        "internal_location": internal_location,
        "job_id": str(job_id) if job_id else None,
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_nonempty_text(body_text: str | None) -> bool:
    return body_text is not None and body_text.strip() != ""


def _audit(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: SiteLogAuditAction,
    detail: dict,
) -> SiteLogEventAuditLog:
    return SiteLogEventAuditLog(
        tenant_id=tenant_id,
        site_log_event_id=event_id,
        actor_user_id=actor_id,
        action=action.value,
        changed_fields=validate_audit_detail(detail),
    )


def _evidence_audit(
    evidence_id: uuid.UUID, actor_id: uuid.UUID, action: str, detail: dict
) -> EvidenceAuditLog:
    return EvidenceAuditLog(
        evidence_id=evidence_id,
        actor_user_id=actor_id,
        action=action,
        detail=validate_audit_detail(detail),
    )


def _is_retryable(exc: BaseException) -> bool:
    """Implementation ruling: connection_invalidated or SQLSTATE
    40001 / 40P01 / 55P03 only. IntegrityError is never retried."""
    if isinstance(exc, IntegrityError):
        return False
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
        exc.orig, "pgcode", None
    )
    return sqlstate in RETRYABLE_SQLSTATES


async def _capped(chunks: AsyncIterator[bytes], max_bytes: int):
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise SiteLogTooLarge()
        yield chunk


# --------------------------------------------------------- lock helpers
# Global order: event → manifest rows (attachment_id asc) → Evidence rows
# (evidence_id asc). Every function below acquires in this order and no
# other. The lock-order test parses this module for the call order.
#
# Every locking read carries ``populate_existing``: a CAS decision must be
# taken on the row as locked in the database, never on an identity-map
# copy left over from an earlier transaction in the same request (the
# declare → inline upload → finalize chain reuses one session).
_FRESH = {"populate_existing": True}


async def _lock_event(
    db: AsyncSession, event_id: uuid.UUID
) -> SiteLogEvent | None:
    q = (
        select(SiteLogEvent)
        .where(
            SiteLogEvent.site_log_event_id == event_id,
            SiteLogEvent.tenant_id == TENANT_ID,
        )
        .with_for_update()
        .execution_options(**_FRESH)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _lock_attachments(
    db: AsyncSession, event: SiteLogEvent
) -> list[SiteLogEventAttachment]:
    q = (
        select(SiteLogEventAttachment)
        .where(
            SiteLogEventAttachment.site_log_event_id == event.site_log_event_id,
            SiteLogEventAttachment.tenant_id == event.tenant_id,
        )
        .order_by(SiteLogEventAttachment.attachment_id)
        .with_for_update()
        .execution_options(**_FRESH)
    )
    return list((await db.execute(q)).scalars().all())


async def _lock_attachment(
    db: AsyncSession, event: SiteLogEvent, attachment_client_id: uuid.UUID
) -> SiteLogEventAttachment | None:
    q = (
        select(SiteLogEventAttachment)
        .where(
            SiteLogEventAttachment.site_log_event_id == event.site_log_event_id,
            SiteLogEventAttachment.tenant_id == event.tenant_id,
            SiteLogEventAttachment.attachment_client_id == attachment_client_id,
        )
        .with_for_update()
        .execution_options(**_FRESH)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _lock_evidence_rows(
    db: AsyncSession, evidence_ids: list[uuid.UUID]
) -> list[Evidence]:
    if not evidence_ids:
        return []
    # ``of=Evidence``: the model eager-joins ``users`` (nullable side of an
    # outer join), which Postgres refuses to lock; only evidence rows lock.
    q = (
        select(Evidence)
        .where(Evidence.evidence_id.in_(evidence_ids))
        .order_by(Evidence.evidence_id)
        .with_for_update(of=Evidence)
        .execution_options(**_FRESH)
    )
    return list((await db.execute(q)).scalars().all())


@contextlib.asynccontextmanager
async def _lock_scope(db: AsyncSession) -> AsyncIterator[AsyncSessionTransaction]:
    """SAVEPOINT that owns every row lock taken inside it.

    Transaction ownership rule (founder ruling B): a negative or no-write
    Site Log result must neither commit nor discard the caller's unrelated
    state. So:

    * ``begin_nested`` first flushes the caller's pending state into the
      caller's own outer transaction (SQLAlchemy semantics, pinned by the
      sentinel tests), then opens a SAVEPOINT; every ``FOR UPDATE`` read
      in the block is taken inside it.
    * a domain exception, or an explicit ``await sp.rollback()`` before a
      no-write return, rolls back only the SAVEPOINT: Postgres releases
      the locks acquired inside it, the outer transaction stays open and
      untouched, and SQLAlchemy expires only objects written inside the
      SAVEPOINT — the caller's ``user``/``event`` remain readable without
      lazy IO.
    * a positive outcome releases the SAVEPOINT; the writes stay in the
      outer transaction until the explicit ``db.commit()`` that the
      positive path issues afterwards (same convention as
      ``services/evidence.py``: short transactions around byte streaming).

    Never ``db.commit()`` / ``db.rollback()`` here.
    """
    sp = await db.begin_nested()
    try:
        yield sp
    except BaseException:
        if sp.is_active:
            await sp.rollback()
        raise
    else:
        if sp.is_active:
            await sp.commit()  # RELEASE SAVEPOINT — not a transaction commit


async def _next_transition_no(db: AsyncSession, event: SiteLogEvent) -> int:
    # Caller holds the event lock: monotonic by construction.
    q = select(CaptureEligibilityTransition.transition_no).where(
        CaptureEligibilityTransition.site_log_event_id == event.site_log_event_id
    )
    nos = list((await db.execute(q)).scalars().all())
    return (max(nos) + 1) if nos else 1


# ------------------------------------------------------- read helpers


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


# ---------------------------------------------------------------- declare


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
            body_text=body_text,
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
    body_text: str,
    max_bytes: int,
) -> bool:
    """Upload the inline row if it is awaiting/failed; leave pending alone
    (2.1 §1). Returns True when the upload was attempted and failed."""

    async def _one_chunk():
        yield body_text.encode("utf-8")  # exact bytes, no normalisation

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
        )
        return False
    except SiteLogUploadInProgress:
        return False  # pending@N — never touched by a replay
    except (EvidenceStorageError, SiteLogTooLarge):
        return True


# ----------------------------------------------------------------- upload


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
) -> tuple[SiteLogEventAttachment, Evidence, int, bool]:
    """Upload Txn A (2.1 §2 rows 1 and 9). Commits.

    Returns ``(attachment, evidence, attempt_no, replay)``. ``replay`` is
    True when the row is already ``stored`` — nothing is written.
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
) -> None:
    """Rows 4/5: manifest pending→failed and Evidence pending→failed,
    atomically, under the global lock order. Commits on write; a
    superseded or vanished row is a no-write return."""
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
                    {"reason": reason, "attempt_no": attempt_no},
                )
            )
        db.add(
            _audit(
                event.site_log_event_id, event.tenant_id, actor.user_id,
                SiteLogAuditAction.attachment_state_changed,
                {"attachment_client_id": str(att.attachment_client_id),
                 "from": "pending", "to": "failed",
                 "attempt_no": attempt_no, "reason": reason},
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
) -> UploadResult:
    """Phase 2 orchestration: Txn A → stream (no lock) → Txn B / fail."""
    att, evidence, attempt_no, replay = await acquire_attachment(
        db, user=user, event_id=event_id,
        attachment_client_id=attachment_client_id, mime_type=mime_type,
    )
    if replay:
        return UploadResult(attachment=att, evidence=evidence, replay=True)

    attachment_id = att.attachment_id
    evidence_id = evidence.evidence_id
    try:
        stored = await storage.put(
            str(evidence_id), _capped(chunks, max_bytes), attempt_no=attempt_no
        )
    except ObjectAlreadyExists as exc:
        # Identical bytes to an earlier attempt: adopt the existing object.
        key = str(exc)
        if not await storage.exists(key):
            await _fail_attachment(
                db, actor=user, event_id=event_id, attachment_id=attachment_id,
                attempt_no=attempt_no, reason="storage_error",
            )
            raise EvidenceStorageError("collision without object") from exc
        stored = await _adopt(storage, key)
    except SiteLogTooLarge:
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


async def _adopt(storage: EvidenceStorage, key: str) -> StoredObject:
    """Rebuild a StoredObject for an existing identical-content object by
    re-reading it (size + sha verified from bytes, never trusted)."""
    hasher = hashlib.sha256()
    size = 0
    async for chunk in storage.open(key):
        hasher.update(chunk)
        size += len(chunk)
    return StoredObject(key=key, size_bytes=size, sha256=hasher.hexdigest())


# ------------------------------------------------------------ admin reset


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
