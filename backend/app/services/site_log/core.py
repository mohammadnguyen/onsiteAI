"""Shared primitives: constants, locking, savepoints and audit rows.

The leaf of the package. Everything imports this; it imports nothing from
the package except :mod:`errors`, so there is no cycle to reason about.

**Global lock order** for every multi-row write: event → manifest rows
(``attachment_id`` asc) → Evidence rows (``evidence_id`` asc). No lock is
ever held across byte streaming. The lock-order guard parses every module in
this package for the call order, in both the bare-name and module-qualified
forms.

**Transaction ownership** (founder ruling B): every locked section runs in
:func:`_lock_scope` — a SAVEPOINT. A denial, conflict, not-found or no-write
replay rolls back only that SAVEPOINT; only a positive write path commits.
Two callers deliberately do NOT use it, each for a documented reason: see
``upload._complete_once`` and ``capture.declare_capture``.

The logger is named for the package, not the module, so a single logger name
covers the whole service.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.models.evidence import Evidence, EvidenceAuditLog
from app.models.site_log import (
    CaptureEligibilityTransition,
    SiteLogAuditAction,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
    validate_audit_detail,
)
from app.services.site_log_access import TENANT_ID

from .errors import SiteLogTooLarge

logger = logging.getLogger("app.services.site_log")

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
