"""Deterministic rules shared by create and update: title and note
cleaning, the create fingerprint, the decision rule, audit rows and the job
check.

No orchestration and no commits here. Everything that can refuse raises
this service's own errors; only the pure proposed-title derivation is taken
from the task service, so both features prefill from an entry the same way.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.potential_variation import (
    NOTE_MAX_CHARS,
    TITLE_MAX_CHARS,
    PotentialVariationAuditLog,
    PvAuditAction,
    PvEndReason,
    PvReviewState,
    validate_pv_audit_detail,
)
from app.models.user import User
from app.services.site_log_access import TENANT_ID, can_read_job
from app.services.tasks.rules import proposed_title_from

from .errors import PvJobCompleted, PvJobNotFound, PvValidationError

__all__ = [
    "audit_row",
    "check_decision",
    "clean_note",
    "clean_title",
    "create_fingerprint",
    "proposed_title_from",
    "str_or_none",
    "target_job",
]


def clean_title(title: str | None) -> str:
    """Trim; refuse blank or over-long titles (422)."""
    if title is None:
        raise PvValidationError("title is required")
    cleaned = title.strip()
    if not cleaned:
        raise PvValidationError("title must contain non-whitespace text")
    if len(cleaned) > TITLE_MAX_CHARS:
        raise PvValidationError(f"title must be at most {TITLE_MAX_CHARS} characters")
    return cleaned


def clean_note(note: str | None) -> str | None:
    """Trim; a blank note is no note; refuse over-long notes (422)."""
    if note is None:
        return None
    cleaned = note.strip()
    if not cleaned:
        return None
    if len(cleaned) > NOTE_MAX_CHARS:
        raise PvValidationError(f"note must be at most {NOTE_MAX_CHARS} characters")
    return cleaned


def check_decision(
    state: PvReviewState | None,
    reason: PvEndReason | None,
    note: str | None,
) -> None:
    """One decision is the state, the end reason and the note together.

    Ending needs a reason; "other" needs a note; any other state carries no
    reason (re-opening clears it on the row; the audit keeps it). ``note`` is
    the cleaned note.
    """
    if state is None:
        raise PvValidationError("review_state is required")
    if state == PvReviewState.ended:
        if reason is None:
            raise PvValidationError("ending follow-up needs a reason")
        if reason == PvEndReason.other and note is None:
            raise PvValidationError('the reason "other" needs a note')
    elif reason is not None:
        raise PvValidationError("only an ended follow-up has a reason")


def create_fingerprint(
    *,
    source_site_log_event_id: uuid.UUID,
    source_revision_no: int,
    job_id: uuid.UUID | None,
    title: str,
) -> str:
    """Canonical hash of what a create asked for (idempotency-key misuse
    detection only): the entry, the version and the job (``None``: no job)
    the person confirmed, and the cleaned title. A resend under the same key
    must match all four to be a replay."""
    canonical = {
        "job_id": str_or_none(job_id),
        "source_revision_no": source_revision_no,
        "source_site_log_event_id": str(source_site_log_event_id),
        "title": title,
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def audit_row(
    pv_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: PvAuditAction,
    detail: dict,
) -> PotentialVariationAuditLog:
    return PotentialVariationAuditLog(
        tenant_id=TENANT_ID,
        potential_variation_id=pv_id,
        actor_user_id=actor_id,
        action=action.value,
        changed_fields=validate_pv_audit_detail(detail),
    )


def str_or_none(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


async def target_job(db: AsyncSession, actor: User, job_id: uuid.UUID) -> Job:
    """The same rule the Site Log and Task use for a job a record is put
    under: it must exist, be readable and not be completed."""
    job = await db.get(Job, job_id)
    if job is None or not can_read_job(actor, job):
        raise PvJobNotFound()
    if job.status == JobStatus.completed:
        raise PvJobCompleted()
    return job
