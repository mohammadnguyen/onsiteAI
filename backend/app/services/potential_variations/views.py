"""Reading flags: access resolution, the view model, and the read entry
points. No writes.

Source protection (DEC-PV-001): whether the caller may read the source entry
is worked out per flag, at read time, with the entry's own rule
(``can_read_event``). If not, nothing derived from the entry leaves the
server — no title (it was prefilled from the entry's text), no pinned text,
no attachment summary — in lists, the detail and the recovery lookup alike.
The builder's note is resolved here too: only the builder gets it.

A view never carries the proposed title: that lives only in the audit,
which no endpoint returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.potential_variation import (
    PotentialVariation,
    PotentialVariationAuditLog,
    PvAuditAction,
    PvReviewState,
)
from app.models.site_log import (
    AttachmentState,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventRevision,
)
from app.models.user import User
from app.services.site_log.inline import inline_attachment_id
from app.services.site_log_access import TENANT_ID, can_read_event

from .access import (
    can_change_job,
    can_decide,
    can_read_note,
    can_set_job,
    can_view_pv,
    visible_pvs_clause,
)
from .errors import PvNotFound, PvSourceNotFound, PvValidationError

PV_PAGE_DEFAULT = 20
PV_PAGE_MAX = 50

# Every read here is taken as the row stands in the database, never as an
# identity-map copy left from earlier in the same session.
_FRESH = {"populate_existing": True}


@dataclass(frozen=True)
class PvPermissions:
    """What the caller may do with this flag, computed on the server so the
    app shows controls without restating the rules."""

    decide: bool
    read_note: bool
    set_job: bool
    change_job: bool


@dataclass(frozen=True)
class PinnedRevision:
    """The entry version the person confirmed when flagging."""

    revision_no: int
    body_text: str | None
    internal_location: str | None
    occurred_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class AttachmentSummary:
    """One of the entry's attachments AS THEY ARE NOW. Attachments belong to
    the entry, not to a version: this is never a snapshot of the pinned
    revision. The inline-text row the server keeps is left out."""

    declared_media_type: str
    state: AttachmentState


@dataclass(frozen=True)
class SourceDetail:
    """Detail-only source block; every field is None when the caller may not
    read the entry."""

    pinned: PinnedRevision | None
    current_revision_no: int | None
    current_withdrawn: bool | None
    attachments: list[AttachmentSummary] | None


@dataclass
class PvView:
    pv: PotentialVariation
    job: Job | None
    creator: User | None
    decided_by: User | None
    # Whether the caller may read the source entry right now.
    source_readable: bool
    # The flag's title, or None when the caller may not read the entry.
    title: str | None
    # The builder's note, or None for anyone but the builder.
    decision_note: str | None
    # The entry's CURRENT job, only when the caller may read the entry, so
    # the app can note "the source entry is now under another job".
    source_job_id: uuid.UUID | None
    permissions: PvPermissions
    # The source entry row, kept for the detail read; never serialised.
    event: SiteLogEvent | None
    detail: SourceDetail | None = None


def _permissions(user: User, pv: PotentialVariation) -> PvPermissions:
    return PvPermissions(
        decide=can_decide(user),
        read_note=can_read_note(user),
        set_job=can_set_job(user, pv),
        change_job=can_change_job(user, pv),
    )


async def build_views(
    db: AsyncSession, user: User, pvs: list[PotentialVariation]
) -> list[PvView]:
    """Views for already-visible flags, with batched lookups."""
    if not pvs:
        return []
    event_ids = {p.source_site_log_event_id for p in pvs}
    events = {
        e.site_log_event_id: e
        for e in (
            await db.execute(
                select(SiteLogEvent)
                .where(
                    SiteLogEvent.site_log_event_id.in_(event_ids),
                    SiteLogEvent.tenant_id == TENANT_ID,
                )
                .execution_options(**_FRESH)
            )
        ).scalars()
    }
    job_ids = {p.job_id for p in pvs if p.job_id} | {
        e.job_id for e in events.values() if e.job_id
    }
    jobs: dict[uuid.UUID, Job] = {}
    if job_ids:
        jobs = {
            j.job_id: j
            for j in (
                await db.execute(select(Job).where(Job.job_id.in_(job_ids)))
            ).scalars()
        }
    user_ids = {p.created_by_user_id for p in pvs} | {
        p.decided_by_user_id for p in pvs if p.decided_by_user_id
    }
    users = {
        u.user_id: u
        for u in (
            await db.execute(select(User).where(User.user_id.in_(user_ids)))
        ).scalars()
    }

    note_ok = can_read_note(user)
    views = []
    for p in pvs:
        event = events.get(p.source_site_log_event_id)
        event_job = jobs.get(event.job_id) if event and event.job_id else None
        readable = event is not None and can_read_event(user, event, event_job)
        views.append(
            PvView(
                pv=p,
                job=jobs.get(p.job_id) if p.job_id else None,
                creator=users.get(p.created_by_user_id),
                decided_by=users.get(p.decided_by_user_id) if p.decided_by_user_id else None,
                source_readable=readable,
                title=p.title if readable else None,
                decision_note=p.decision_note if note_ok else None,
                source_job_id=event.job_id if readable and event is not None else None,
                permissions=_permissions(user, p),
                event=event,
            )
        )
    return views


async def build_view(db: AsyncSession, user: User, pv: PotentialVariation) -> PvView:
    return (await build_views(db, user, [pv]))[0]


async def build_detail(db: AsyncSession, user: User, pv: PotentialVariation) -> PvView:
    """The view plus the source block: the pinned revision exactly as it was
    confirmed, the entry's current version number and withdrawal state, and
    the entry's current attachments - all only when the caller may read the
    entry."""
    view = await build_view(db, user, pv)
    event = view.event
    if not view.source_readable or event is None:
        view.detail = SourceDetail(
            pinned=None, current_revision_no=None, current_withdrawn=None, attachments=None
        )
        return view

    pinned = (
        await db.execute(
            select(SiteLogEventRevision)
            .where(
                SiteLogEventRevision.site_log_event_id == event.site_log_event_id,
                SiteLogEventRevision.tenant_id == TENANT_ID,
                SiteLogEventRevision.revision_no == pv.source_revision_no,
            )
            .execution_options(**_FRESH)
        )
    ).scalar_one_or_none()
    latest = await current_revision(db, event)

    inline_id = inline_attachment_id(event.capture_client_id)
    attachments = (
        await db.execute(
            select(SiteLogEventAttachment)
            .where(
                SiteLogEventAttachment.site_log_event_id == event.site_log_event_id,
                SiteLogEventAttachment.tenant_id == TENANT_ID,
            )
            .order_by(SiteLogEventAttachment.created_at, SiteLogEventAttachment.attachment_id)
            .execution_options(**_FRESH)
        )
    ).scalars().all()

    view.detail = SourceDetail(
        pinned=(
            PinnedRevision(
                revision_no=pinned.revision_no,
                body_text=pinned.body_text,
                internal_location=pinned.internal_location,
                occurred_at=pinned.occurred_at,
                created_at=pinned.created_at,
            )
            if pinned is not None
            else None
        ),
        current_revision_no=latest.revision_no if latest is not None else None,
        current_withdrawn=latest.withdrawn if latest is not None else None,
        attachments=[
            AttachmentSummary(declared_media_type=a.declared_media_type, state=a.state)
            for a in attachments
            # By identity, never by media type: a .txt the user attached is
            # "text" too and must stay listed.
            if a.attachment_client_id != inline_id
        ],
    )
    return view


async def load_visible_pv(
    db: AsyncSession, user: User, pv_id: uuid.UUID, *, lock: bool = False
) -> PotentialVariation:
    """The flag, or PvNotFound when it is unknown or not visible to the
    caller (existence hidden). ``lock`` takes a row lock; the caller then
    owns the transaction."""
    q = (
        select(PotentialVariation)
        .where(
            PotentialVariation.potential_variation_id == pv_id,
            PotentialVariation.tenant_id == TENANT_ID,
        )
        .execution_options(**_FRESH)
    )
    if lock:
        q = q.with_for_update()
    pv = (await db.execute(q)).scalar_one_or_none()
    if pv is None or not can_view_pv(user, pv):
        raise PvNotFound()
    return pv


async def readable_source(
    db: AsyncSession, user: User, event_id: uuid.UUID
) -> SiteLogEvent:
    """The Site Log entry, or PvSourceNotFound when the caller may not read
    it. The entry's own rule applies unchanged."""
    event = (
        await db.execute(
            select(SiteLogEvent)
            .where(
                SiteLogEvent.site_log_event_id == event_id,
                SiteLogEvent.tenant_id == TENANT_ID,
            )
            .execution_options(**_FRESH)
        )
    ).scalar_one_or_none()
    job = None
    if event is not None and event.job_id is not None:
        job = await db.get(Job, event.job_id)
    if event is None or not can_read_event(user, event, job):
        raise PvSourceNotFound()
    return event


async def current_revision(
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
        .execution_options(**_FRESH)
    )
    return (await db.execute(q)).scalar_one()


async def created_fingerprint(db: AsyncSession, pv: PotentialVariation) -> str | None:
    q = select(PotentialVariationAuditLog).where(
        PotentialVariationAuditLog.potential_variation_id == pv.potential_variation_id,
        PotentialVariationAuditLog.action == PvAuditAction.created.value,
    )
    row = (await db.execute(q)).scalar_one_or_none()
    return None if row is None else row.changed_fields.get("create_fingerprint")


# ------------------------------------------------------------------ reads


async def get_pv(db: AsyncSession, user: User, pv_id: uuid.UUID) -> PvView:
    pv = await load_visible_pv(db, user, pv_id)
    return await build_detail(db, user, pv)


async def list_pvs(
    db: AsyncSession,
    user: User,
    *,
    review_state: PvReviewState | None = None,
    job_id: uuid.UUID | None = None,
    no_job: bool = False,
    create_client_id: uuid.UUID | None = None,
    limit: int = PV_PAGE_DEFAULT,
    offset: int = 0,
) -> list[PvView]:
    """Flags the caller may view, newest first, one capped page at a time.

    ``no_job`` lists flags that have no job yet (a flag from a job-less
    entry). ``create_client_id`` narrows to the caller's OWN create with that
    key — how the app learns whether a create whose answer was lost went
    through.
    """
    if no_job and job_id is not None:
        raise PvValidationError("job_id and no_job cannot be combined")
    q = select(PotentialVariation).where(PotentialVariation.tenant_id == TENANT_ID)
    clause = visible_pvs_clause(user)
    if clause is not None:
        q = q.where(clause)
    if create_client_id is not None:
        q = q.where(
            PotentialVariation.created_by_user_id == user.user_id,
            PotentialVariation.create_client_id == create_client_id,
        )
    if review_state is not None:
        q = q.where(PotentialVariation.review_state == review_state)
    if job_id is not None:
        q = q.where(PotentialVariation.job_id == job_id)
    if no_job:
        q = q.where(PotentialVariation.job_id.is_(None))
    q = (
        q.order_by(
            PotentialVariation.created_at.desc(),
            PotentialVariation.potential_variation_id.desc(),
        )
        .limit(max(1, min(limit, PV_PAGE_MAX)))
        .offset(max(0, offset))
        .execution_options(**_FRESH)
    )
    pvs = list((await db.execute(q)).scalars().all())
    return await build_views(db, user, pvs)


async def list_event_pvs(
    db: AsyncSession, user: User, event_id: uuid.UUID
) -> list[PvView]:
    """The flags made from one entry that the caller may view. The caller
    must be able to read the entry itself (404 otherwise)."""
    await readable_source(db, user, event_id)
    q = select(PotentialVariation).where(
        PotentialVariation.tenant_id == TENANT_ID,
        PotentialVariation.source_site_log_event_id == event_id,
    )
    clause = visible_pvs_clause(user)
    if clause is not None:
        q = q.where(clause)
    q = q.order_by(
        PotentialVariation.created_at.desc(),
        PotentialVariation.potential_variation_id.desc(),
    ).execution_options(**_FRESH)
    pvs = list((await db.execute(q)).scalars().all())
    return await build_views(db, user, pvs)
