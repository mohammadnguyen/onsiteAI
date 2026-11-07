"""Flagging a Site Log entry as a Potential Variation (DEC-PV-001).

One explicit human action writes one flag and its ``created`` audit row —
the full record of the confirmation (DEC-EVIDENCE-001). The source entry is
only read.

Idempotency follows the task create exactly: a device-generated
``create_client_id``, unique per tenant and creator; requests for one key
handled one at a time (transaction-scoped advisory lock); a SAVEPOINT
insert; a fingerprint in the created audit row. A replay with the same body
returns the existing flag; the same key with a different body is refused.

The version pin is the difference: the app sends the revision number the
person actually saw and confirmed, and the server refuses (409
``source_changed``) if the entry has moved on since — the person looks
again and confirms again, under a new key. The server never pins a version
the person did not see.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.potential_variation import (
    PotentialVariation,
    PvAuditAction,
    PvOrigin,
    PvReviewState,
)
from app.models.user import User
from app.services.site_log_access import TENANT_ID

from .errors import (
    PvFingerprintMismatch,
    PvSourceChanged,
    PvSourceWithdrawn,
    PvValidationError,
)
from .rules import (
    audit_row,
    clean_title,
    create_fingerprint,
    proposed_title_from,
    str_or_none,
)
from .views import (
    PvView,
    build_view,
    created_fingerprint,
    current_revision,
    readable_source,
)

logger = logging.getLogger("app.services.potential_variations")


@dataclass
class CreateResult:
    view: PvView
    created: bool


def _create_lock_key(creator_id: uuid.UUID, create_client_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for the per-(tenant, creator, create key)
    advisory lock - the task create's scheme, with its own prefix so a flag
    key and a task key never share a lock."""
    digest = hashlib.blake2b(
        f"pv-create:{TENANT_ID}:{creator_id}:{create_client_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _by_client_id(
    db: AsyncSession, user: User, create_client_id: uuid.UUID
) -> PotentialVariation | None:
    q = (
        select(PotentialVariation)
        .where(
            PotentialVariation.tenant_id == TENANT_ID,
            PotentialVariation.created_by_user_id == user.user_id,
            PotentialVariation.create_client_id == create_client_id,
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _replay(
    db: AsyncSession, user: User, existing: PotentialVariation, fingerprint: str
) -> CreateResult:
    if await created_fingerprint(db, existing) != fingerprint:
        raise PvFingerprintMismatch()
    return CreateResult(view=await build_view(db, user, existing), created=False)


async def create_pv(
    db: AsyncSession,
    *,
    user: User,
    event_id: uuid.UUID,
    create_client_id: uuid.UUID,
    title: str | None,
    source_revision_no: int,
) -> CreateResult:
    """Flag entry ``event_id`` or return the flag this key made.

    Order: the title must be usable (422) → this creator's requests for this
    key, one at a time → a key already used is a replay (200) or a misuse
    (409) → the entry must be readable (404) → a withdrawn entry is refused
    (409) → the version the person confirmed must still be the entry's
    current version (409 ``source_changed``) → insert.

    A key already used is answered before any of the checks after it, on
    purpose: they read state that can change between two requests. A flag
    that was saved keeps answering with itself even if the entry was
    corrected or withdrawn since; judged on its own instead, a resend could
    be refused, the app would give up the key, and the first request would
    still have made the flag - a second flag would follow.
    """
    if source_revision_no < 1:
        raise PvValidationError("source_revision_no must be at least 1")
    final_title = clean_title(title)
    fingerprint = create_fingerprint(
        source_site_log_event_id=event_id,
        source_revision_no=source_revision_no,
        title=final_title,
    )
    # Transaction-scoped: released at this request's commit or rollback.
    await db.execute(
        select(func.pg_advisory_xact_lock(_create_lock_key(user.user_id, create_client_id)))
    )

    existing = await _by_client_id(db, user, create_client_id)
    if existing is not None:
        return await _replay(db, user, existing, fingerprint)

    event = await readable_source(db, user, event_id)
    revision = await current_revision(db, event)
    if revision.withdrawn:
        raise PvSourceWithdrawn()
    if revision.revision_no != source_revision_no:
        raise PvSourceChanged()

    proposed = proposed_title_from(revision.body_text)
    pv = PotentialVariation(
        potential_variation_id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        job_id=event.job_id,  # the flag's own copy, never synced later
        source_site_log_event_id=event.site_log_event_id,
        source_revision_no=revision.revision_no,
        title=final_title,
        review_state=PvReviewState.to_check,
        origin=PvOrigin.manual,
        created_by_user_id=user.user_id,
        create_client_id=create_client_id,
    )
    try:
        # SAVEPOINT: a lost race on the unique key rolls back only this
        # insert; the caller's loaded objects stay usable. (The lock above
        # already serialises one key; the unique key stays the backstop.)
        async with db.begin_nested():
            db.add(pv)
            await db.flush()
    except IntegrityError:
        existing = await _by_client_id(db, user, create_client_id)
        if existing is None:
            raise
        return await _replay(db, user, existing, fingerprint)

    db.add(
        audit_row(
            pv.potential_variation_id,
            user.user_id,
            PvAuditAction.created,
            {
                # Evidence ref: the entry and the version the person confirmed.
                "source_site_log_event_id": str(event.site_log_event_id),
                "source_revision_no": revision.revision_no,
                # Proposed (server-derived), human edit, final.
                "proposed_title": proposed,
                "title_edited": final_title != proposed,
                "final_title": final_title,
                "job_id": str_or_none(event.job_id),
                "review_state": PvReviewState.to_check.value,
                "origin": PvOrigin.manual.value,
                "create_fingerprint": fingerprint,
            },
        )
    )
    await db.commit()
    # Server-set timestamps are read back explicitly: touching an unloaded
    # column on an async session would otherwise mean hidden IO.
    await db.refresh(pv)
    # Ids and facts only: never the title, the entry text or a note.
    logger.info(
        "potential variation created pv_id=%s source_event_id=%s revision_no=%d "
        "has_job=%s",
        pv.potential_variation_id,
        event.site_log_event_id,
        revision.revision_no,
        event.job_id is not None,
    )
    return CreateResult(view=await build_view(db, user, pv), created=True)
