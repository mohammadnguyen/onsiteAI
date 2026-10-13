"""Deterministic rules shared by create and update: the proposed-title
derivation, title normalisation, the create fingerprint, audit rows, and
the assignee and job checks.

No orchestration and no commits here.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.task import (
    TITLE_MAX_CHARS,
    TaskAuditAction,
    TaskAuditLog,
    validate_task_audit_detail,
)
from app.models.user import User
from app.services.site_log_access import TENANT_ID, can_read_job

from .access import can_assign_others
from .errors import (
    TaskJobCompleted,
    TaskJobNotFound,
    TaskValidationError,
)

# Line breaks the proposed-title rule splits on. The app's prefill uses the
# same three (mobile/src/tasks/proposedTitle.ts); keep the two in step.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def proposed_title_from(body_text: str | None) -> str | None:
    """The title the app pre-fills, derived on the server from one revision.

    The first line that is not blank, trimmed, cut to 200 characters (code
    points) and trimmed again at the end, so a cut never leaves trailing
    space. None when the revision has no text (an attachments-only entry):
    the user then types a title from scratch.

    The server derives this itself from the stored revision; it never takes
    the client's word for what was proposed.
    """
    if body_text is None:
        return None
    for line in _LINE_BREAK.split(body_text):
        trimmed = line.strip()
        if trimmed:
            return trimmed[:TITLE_MAX_CHARS].rstrip()
    return None


def clean_title(title: str | None) -> str:
    """Trim; refuse blank or over-long titles (422)."""
    if title is None:
        raise TaskValidationError("title is required")
    cleaned = title.strip()
    if not cleaned:
        raise TaskValidationError("title must contain non-whitespace text")
    if len(cleaned) > TITLE_MAX_CHARS:
        raise TaskValidationError(
            f"title must be at most {TITLE_MAX_CHARS} characters"
        )
    return cleaned


def create_fingerprint(
    *,
    source_site_log_event_id: uuid.UUID,
    title: str,
    assignee_user_id: uuid.UUID | None,
    due_date: date | None,
) -> str:
    """Canonical hash of what a create asked for (idempotency-key misuse
    detection only). ``title`` is the cleaned title."""
    canonical = {
        "assignee_user_id": str(assignee_user_id) if assignee_user_id else None,
        "due_date": due_date.isoformat() if due_date else None,
        "source_site_log_event_id": str(source_site_log_event_id),
        "title": title,
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def audit_row(
    task_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: TaskAuditAction,
    detail: dict,
) -> TaskAuditLog:
    return TaskAuditLog(
        tenant_id=TENANT_ID,
        task_id=task_id,
        actor_user_id=actor_id,
        action=action.value,
        changed_fields=validate_task_audit_detail(detail),
    )


def iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value else None


def str_or_none(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


async def check_assignee(
    db: AsyncSession, actor: User, assignee_user_id: uuid.UUID | None
) -> None:
    """D1: admins may pick any active user; everyone else only themselves or
    nobody. Refusals are 422, and say nothing about whether a user exists
    beyond what the caller could already see."""
    if assignee_user_id is None:
        return
    if not can_assign_others(actor) and assignee_user_id != actor.user_id:
        raise TaskValidationError("you can assign a task only to yourself")
    assignee = await db.get(User, assignee_user_id)
    if assignee is None or not assignee.is_active:
        raise TaskValidationError("the assignee must be an active user")


async def target_job(db: AsyncSession, actor: User, job_id: uuid.UUID) -> Job:
    """The same rule the Site Log uses for a job an entry is linked to:
    it must exist, be readable and not be completed."""
    job = await db.get(Job, job_id)
    if job is None or not can_read_job(actor, job):
        raise TaskJobNotFound()
    if job.status == JobStatus.completed:
        raise TaskJobCompleted()
    return job
