"""Creating a task from a Site Log entry (plan v2 §2, §5).

One explicit human action writes one task and its ``created`` audit row —
the full record of the confirmation (D2). The source entry is only read.

Idempotency follows the Site Log declare pattern: a device-generated
``create_client_id``, unique per tenant and creator; a SAVEPOINT insert; a
fingerprint in the created audit row. A replay with the same body returns
the existing task; the same key with a different body is refused.

Requests for one key are handled one at a time (a transaction-scoped
advisory lock, the labour day-lock pattern), and a key already used is
answered before anything that can change between two requests is checked.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskAuditAction, TaskOrigin, TaskStatus
from app.models.user import User
from app.services.site_log_access import TENANT_ID

from .errors import TaskFingerprintMismatch, TaskSourceWithdrawn
from .rules import (
    audit_row,
    check_assignee,
    clean_title,
    create_fingerprint,
    iso_or_none,
    proposed_title_from,
    str_or_none,
)
from .views import (
    TaskView,
    build_view,
    created_fingerprint,
    current_revision,
    readable_source,
)

logger = logging.getLogger("app.services.tasks")


@dataclass
class CreateResult:
    view: TaskView
    created: bool


def _create_lock_key(creator_id: uuid.UUID, create_client_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for the per-(tenant, creator, create key)
    advisory lock - the same scheme as the labour day lock
    (``services/labour.py``): Postgres advisory-lock keys are bigints."""
    digest = hashlib.blake2b(
        f"task-create:{TENANT_ID}:{creator_id}:{create_client_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _by_client_id(
    db: AsyncSession, user: User, create_client_id: uuid.UUID
) -> Task | None:
    q = (
        select(Task)
        .where(
            Task.tenant_id == TENANT_ID,
            Task.created_by_user_id == user.user_id,
            Task.create_client_id == create_client_id,
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _replay(
    db: AsyncSession, user: User, existing: Task, fingerprint: str
) -> CreateResult:
    if await created_fingerprint(db, existing) != fingerprint:
        raise TaskFingerprintMismatch()
    return CreateResult(view=await build_view(db, user, existing), created=False)


async def create_task(
    db: AsyncSession,
    *,
    user: User,
    event_id: uuid.UUID,
    create_client_id: uuid.UUID,
    title: str | None,
    assignee_user_id: uuid.UUID | None,
    due_date: date | None,
) -> CreateResult:
    """Create a task from entry ``event_id`` or return the one this key made.

    Order: the title must be usable (422) → this creator's requests for
    this key, one at a time → a key already used is a replay (200) or a
    misuse (409) → the entry must be readable (404) → a withdrawn entry is
    refused (409) → the assignee must be allowed (422) → insert.

    A key already used is answered before any of the checks after it, on
    purpose: they read state that can change between two requests. A create
    that succeeded keeps answering with its task even if the entry was
    withdrawn, or the assignee deactivated, since. And a resend that arrives
    while the first request is still uncommitted waits for it at the lock:
    if the first commits, the resend replays its task; if it rolls back, the
    resend is judged as a new create. Judged on its own instead, a resend
    could be refused (the assignee deactivated in between), the app would
    give up the key, and the first request would still commit - a second
    task would follow. Waiting at the lock is no answer: an app whose
    request times out meanwhile keeps its key and asks again later.
    """
    final_title = clean_title(title)
    fingerprint = create_fingerprint(
        source_site_log_event_id=event_id,
        title=final_title,
        assignee_user_id=assignee_user_id,
        due_date=due_date,
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
        raise TaskSourceWithdrawn()
    await check_assignee(db, user, assignee_user_id)

    proposed = proposed_title_from(revision.body_text)
    task = Task(
        task_id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        job_id=event.job_id,  # the task's own copy, never synced later
        source_site_log_event_id=event.site_log_event_id,
        source_revision_no=revision.revision_no,
        title=final_title,
        status=TaskStatus.to_do,
        assignee_user_id=assignee_user_id,
        due_date=due_date,
        origin=TaskOrigin.manual,
        created_by_user_id=user.user_id,
        create_client_id=create_client_id,
    )
    try:
        # SAVEPOINT: a lost race on the unique key rolls back only this
        # insert; the caller's loaded objects stay usable. (The lock above
        # already serialises one key; the unique key stays the backstop.)
        async with db.begin_nested():
            db.add(task)
            await db.flush()
    except IntegrityError:
        existing = await _by_client_id(db, user, create_client_id)
        if existing is None:
            raise
        return await _replay(db, user, existing, fingerprint)

    db.add(
        audit_row(
            task.task_id,
            user.user_id,
            TaskAuditAction.created,
            {
                # Evidence ref: the entry and the revision the task was made from.
                "source_site_log_event_id": str(event.site_log_event_id),
                "source_revision_no": revision.revision_no,
                # Proposed (server-derived), human edit, final.
                "proposed_title": proposed,
                "title_edited": final_title != proposed,
                "final_title": final_title,
                "assignee_user_id": str_or_none(assignee_user_id),
                "due_date": iso_or_none(due_date),
                "job_id": str_or_none(event.job_id),
                "origin": TaskOrigin.manual.value,
                "create_fingerprint": fingerprint,
            },
        )
    )
    await db.commit()
    # Server-set timestamps are read back explicitly: touching an unloaded
    # column on an async session would otherwise mean hidden IO.
    await db.refresh(task)
    logger.info(
        "task created task_id=%s source_event_id=%s revision_no=%d has_job=%s "
        "has_assignee=%s has_due_date=%s",
        task.task_id,
        event.site_log_event_id,
        revision.revision_no,
        event.job_id is not None,
        assignee_user_id is not None,
        due_date is not None,
    )
    return CreateResult(view=await build_view(db, user, task), created=True)
