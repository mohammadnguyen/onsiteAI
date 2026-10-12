"""Changing a task: title, status, assignee, due date and job (plan v2 §3).

One PATCH, one row lock, one audit row per field that actually changed. A
field sent with its current value is not a change: it needs no permission
and writes nothing. Nothing here touches the source Site Log entry.

Denial order, as in the Site Log service: not visible → 404; visible but
not allowed to make this change → 403; then validation (422, or 404 for an
unknown job).
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.models.task import Task, TaskAuditAction
from app.models.user import User

from .access import can_change_job, can_change_status, can_edit_task, can_set_job
from .errors import TaskForbidden, TaskReasonRequired, TaskValidationError
from .rules import (
    audit_row,
    check_assignee,
    clean_title,
    iso_or_none,
    str_or_none,
    target_job,
)
from .views import TaskView, build_view, load_visible_task

logger = logging.getLogger("app.services.tasks")

UPDATABLE_FIELDS = frozenset(
    {"title", "status", "assignee_user_id", "due_date", "job_id"}
)
# Fields the creator (or an admin) may edit; status has its own wider rule.
_EDIT_FIELDS = ("title", "assignee_user_id", "due_date")


@contextlib.asynccontextmanager
async def _savepoint(db: AsyncSession) -> AsyncIterator[AsyncSessionTransaction]:
    """SAVEPOINT owning the row lock (the Site Log ``_lock_scope`` rule): a
    denial or a no-op rolls back only this savepoint and leaves the caller's
    transaction untouched; only the positive path commits, afterwards."""
    sp = await db.begin_nested()
    try:
        yield sp
    except BaseException:
        if sp.is_active:
            await sp.rollback()
        raise
    else:
        if sp.is_active:
            await sp.commit()  # RELEASE SAVEPOINT


def _current(task: Task, field: str) -> Any:
    return getattr(task, field)


def _comparable(field: str, value: Any) -> Any:
    # Titles are compared trimmed, the way they are stored.
    if field == "title" and isinstance(value, str):
        return value.strip()
    return value


async def update_task(
    db: AsyncSession,
    *,
    user: User,
    task_id: uuid.UUID,
    changes: dict[str, Any],
    reason: str | None = None,
) -> TaskView:
    """Apply the requested ``changes`` (only the fields the caller sent)."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"not an updatable task field: {sorted(unknown)}")
    if not changes:
        raise TaskValidationError("nothing to change")

    async with _savepoint(db) as sp:
        task, _job = await load_visible_task(db, user, task_id, lock=True)

        diff = [
            f
            for f in changes
            if _comparable(f, changes[f]) != _current(task, f)
        ]

        # -- permissions, for every field that would change (403) --------
        for f in diff:
            if f == "status" and not can_change_status(user, task):
                raise TaskForbidden()
            if f in _EDIT_FIELDS and not can_edit_task(user, task):
                raise TaskForbidden()
            if f == "job_id":
                if task.job_id is None:
                    if not can_set_job(user, task):
                        raise TaskForbidden()
                elif not can_change_job(user, task):
                    raise TaskForbidden("Admin only")

        # -- validation (422 / 404) ----------------------------------------
        new_title = clean_title(changes["title"]) if "title" in diff else None
        if "status" in diff and changes["status"] is None:
            raise TaskValidationError("status is required")
        if "assignee_user_id" in diff:
            await check_assignee(db, user, changes["assignee_user_id"])
        clean_reason = (reason or "").strip() or None
        if "job_id" in diff:
            if changes["job_id"] is None:
                raise TaskValidationError("a task's job can be changed but not removed")
            if task.job_id is not None and clean_reason is None:
                raise TaskReasonRequired()
            await target_job(db, user, changes["job_id"])

        if not diff:
            await sp.rollback()  # a pure no-op: no write, no audit
            return await build_view(db, user, task)

        # -- apply, one audit row per changed field ------------------------
        rows = []
        if "title" in diff:
            rows.append(
                (TaskAuditAction.title_changed, {"from": task.title, "to": new_title})
            )
            task.title = new_title
        if "status" in diff:
            new_status = changes["status"]
            rows.append(
                (
                    TaskAuditAction.status_changed,
                    {"from": task.status.value, "to": new_status.value},
                )
            )
            task.status = new_status
        if "assignee_user_id" in diff:
            new_assignee = changes["assignee_user_id"]
            rows.append(
                (
                    TaskAuditAction.assignee_changed,
                    {
                        "from": str_or_none(task.assignee_user_id),
                        "to": str_or_none(new_assignee),
                    },
                )
            )
            task.assignee_user_id = new_assignee
        if "due_date" in diff:
            new_due = changes["due_date"]
            rows.append(
                (
                    TaskAuditAction.due_date_changed,
                    {"from": iso_or_none(task.due_date), "to": iso_or_none(new_due)},
                )
            )
            task.due_date = new_due
        if "job_id" in diff:
            new_job_id = changes["job_id"]
            rows.append(
                (
                    TaskAuditAction.job_set,
                    {
                        "from": str_or_none(task.job_id),
                        "to": str(new_job_id),
                        "reason": clean_reason,
                    },
                )
            )
            task.job_id = new_job_id
        for action, detail in rows:
            db.add(audit_row(task.task_id, user.user_id, action, detail))

    await db.commit()
    # updated_at is set by the database on UPDATE: read it back explicitly.
    await db.refresh(task)
    logger.info(
        "task updated task_id=%s fields=%s",
        task.task_id,
        ",".join(sorted(diff)),
    )
    return await build_view(db, user, task)
