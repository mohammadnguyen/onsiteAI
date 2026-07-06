"""PR 2 — global soft-delete filter behaviour.

Covers the four scoped concerns plus a blast-radius guard:

1. Default ORM SELECTs exclude ``deleted_at IS NOT NULL`` rows.
2. ``execution_options(include_deleted=True)`` bypasses the filter.
3. Relationship loads (selectinload) also exclude soft-deleted rows, via
   ``with_loader_criteria(propagate_to_loaders=True)``.
4. The append-only ``timeline_audit_log`` (no ``SoftDeleteMixin``) is
   untouched — proving the filter never appends a bogus ``deleted_at``
   predicate to a non-soft-deletable entity.
5. An existing non-Timeline entity (``Job``) is likewise unaffected.

The global ``do_orm_execute`` listener is registered on the base
``Session`` class in :mod:`app.database`, so it is active for the
conftest-built ``AsyncSession`` too (which wraps that same class).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    Job,
    JobStatus,
    TimelineAttachment,
    TimelineAuditLog,
    TimelineItem,
    TimelineItemType,
)


async def _make_job(db_session, admin, *, name: str = "Kelly House") -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def _make_item(
    db_session, job, admin, *, deleted: bool = False
) -> TimelineItem:
    item = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=job.job_id,
        item_type=TimelineItemType.daily_note,
        body="note",
        occurred_at=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        created_by=admin.user_id,
        deleted_at=datetime(2026, 7, 6, 12, 0, tzinfo=UTC) if deleted else None,
    )
    db_session.add(item)
    await db_session.flush()
    return item


@pytest.mark.asyncio
async def test_default_query_excludes_soft_deleted(db_session, seeded_admin):
    """A plain ``select`` returns only the live row, not the soft-deleted one."""
    job = await _make_job(db_session, seeded_admin)
    live = await _make_item(db_session, job, seeded_admin, deleted=False)
    await _make_item(db_session, job, seeded_admin, deleted=True)

    rows = (
        await db_session.execute(
            select(TimelineItem).where(TimelineItem.job_id == job.job_id)
        )
    ).scalars().all()

    assert [r.timeline_item_id for r in rows] == [live.timeline_item_id]


@pytest.mark.asyncio
async def test_include_deleted_escape_hatch(db_session, seeded_admin):
    """``execution_options(include_deleted=True)`` returns soft-deleted rows too."""
    job = await _make_job(db_session, seeded_admin)
    live = await _make_item(db_session, job, seeded_admin, deleted=False)
    dead = await _make_item(db_session, job, seeded_admin, deleted=True)

    rows = (
        await db_session.execute(
            select(TimelineItem)
            .where(TimelineItem.job_id == job.job_id)
            .execution_options(include_deleted=True)
        )
    ).scalars().all()

    assert {r.timeline_item_id for r in rows} == {
        live.timeline_item_id,
        dead.timeline_item_id,
    }


@pytest.mark.asyncio
async def test_get_excludes_soft_deleted_with_escape_hatch(
    db_session, seeded_admin
):
    """``Session.get`` is filtered; the execution-option escape hatch restores it.

    ``expunge_all`` first so ``get`` must re-issue SQL (an identity-map hit
    would short-circuit the query and bypass the filter).
    """
    job = await _make_job(db_session, seeded_admin)
    dead = await _make_item(db_session, job, seeded_admin, deleted=True)
    dead_id = dead.timeline_item_id
    db_session.expunge_all()

    assert await db_session.get(TimelineItem, dead_id) is None

    restored = await db_session.get(
        TimelineItem, dead_id, execution_options={"include_deleted": True}
    )
    assert restored is not None
    assert restored.timeline_item_id == dead_id


@pytest.mark.asyncio
async def test_relationship_load_excludes_soft_deleted(db_session, seeded_admin):
    """selectinload of a collection excludes soft-deleted children by default."""
    job = await _make_job(db_session, seeded_admin)
    item = await _make_item(db_session, job, seeded_admin, deleted=False)

    live_att = TimelineAttachment(
        attachment_id=uuid.uuid4(),
        timeline_item_id=item.timeline_item_id,
        storage_key="k/live.jpg",
        content_type="image/jpeg",
        created_by=seeded_admin.user_id,
    )
    dead_att = TimelineAttachment(
        attachment_id=uuid.uuid4(),
        timeline_item_id=item.timeline_item_id,
        storage_key="k/dead.jpg",
        content_type="image/jpeg",
        created_by=seeded_admin.user_id,
        deleted_at=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
    )
    db_session.add_all([live_att, dead_att])
    await db_session.flush()
    db_session.expunge_all()

    loaded = (
        await db_session.execute(
            select(TimelineItem)
            .where(TimelineItem.timeline_item_id == item.timeline_item_id)
            .options(selectinload(TimelineItem.attachments))
        )
    ).scalar_one()

    assert [a.attachment_id for a in loaded.attachments] == [
        live_att.attachment_id
    ]

    # Escape hatch: with include_deleted, no criteria is added or propagated,
    # so the collection load returns both children.
    db_session.expunge_all()
    loaded_all = (
        await db_session.execute(
            select(TimelineItem)
            .where(TimelineItem.timeline_item_id == item.timeline_item_id)
            .options(selectinload(TimelineItem.attachments))
            .execution_options(include_deleted=True)
        )
    ).scalar_one()

    assert {a.attachment_id for a in loaded_all.attachments} == {
        live_att.attachment_id,
        dead_att.attachment_id,
    }


@pytest.mark.asyncio
async def test_audit_log_not_filtered(db_session, seeded_admin):
    """``timeline_audit_log`` has no ``deleted_at``; the filter must skip it.

    If the listener wrongly applied ``deleted_at IS NULL`` to this entity,
    the query would raise (no such column). Getting the row back proves the
    filter is correctly scoped to SoftDeleteMixin subclasses.
    """
    job = await _make_job(db_session, seeded_admin)
    entry = TimelineAuditLog(
        audit_id=uuid.uuid4(),
        timeline_item_id=uuid.uuid4(),
        job_id=job.job_id,
        action="create",
        actor_user_id=seeded_admin.user_id,
        detail={"k": "v"},
    )
    db_session.add(entry)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(TimelineAuditLog).where(
                TimelineAuditLog.job_id == job.job_id
            )
        )
    ).scalars().all()

    assert [r.audit_id for r in rows] == [entry.audit_id]


@pytest.mark.asyncio
async def test_non_softdelete_entity_unaffected(db_session, seeded_admin):
    """An existing entity without the mixin (``Job``) is returned normally."""
    job = await _make_job(db_session, seeded_admin, name="Unaffected Job")

    fetched = (
        await db_session.execute(
            select(Job).where(Job.job_id == job.job_id)
        )
    ).scalar_one()

    assert fetched.job_id == job.job_id
