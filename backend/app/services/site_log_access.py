"""Read-access helpers for the Site Log capture layer (WP A A2a).

Pure/thin helpers only — this module writes nothing. It exists so that
the Site Log service and the refactored Evidence service share ONE Job
access rule and ONE event access rule instead of each hard-coding
"job_id is not None". When Job access tightens later, both surfaces
follow automatically.

Rules (founder rulings O3/O5, Revision 2 §5):

* unassigned event (``job_id IS NULL``): author and admin only;
* assigned event: the linked Job's access rule via :func:`can_read_job`;
* Evidence bound to an event inherits the event's rule — the bound
  branch is looked up here so ``services/evidence.py`` never re-derives
  it;
* withdrawal never changes the access population (A2b concern, the
  helper is already withdrawal-agnostic).

V1 single-tenant reality, stated plainly: ``can_read_job`` evaluates to
"any active authenticated user" today. The point of the helper is that
this sentence lives in exactly one place.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.site_log import SiteLogEvent, SiteLogEventAttachment
from app.models.user import User, UserRole

# Single-tenant default shared by every WP A table (server_default on the
# columns). Every query filters on it and every child row copies it from
# its locked parent — never from client input (Revision 2 §5).
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def can_read_job(user: User, job: Job | None) -> bool:
    """The single Job read rule. V1: any active authenticated user."""
    return job is not None and bool(user.is_active)


def can_read_event(user: User, event: SiteLogEvent, job: Job | None) -> bool:
    """Event read rule; ``job`` must be the event's linked Job (or None)."""
    if user.role == UserRole.admin:
        return True
    if event.author_user_id == user.user_id:
        return True
    if event.job_id is None:
        return False
    return can_read_job(user, job)


async def binding_for_evidence(
    db: AsyncSession, evidence_id: uuid.UUID
) -> SiteLogEventAttachment | None:
    """The manifest row that binds ``evidence_id``, if any (at most one —
    partial UNIQUE on ``evidence_id``)."""
    q = select(SiteLogEventAttachment).where(
        SiteLogEventAttachment.evidence_id == evidence_id
    )
    return (await db.execute(q)).scalar_one_or_none()


async def load_event_for_access(
    db: AsyncSession, event_id: uuid.UUID
) -> tuple[SiteLogEvent | None, Job | None]:
    """Tenant-filtered event + its Job (no lock; read path only)."""
    q = select(SiteLogEvent).where(
        SiteLogEvent.site_log_event_id == event_id,
        SiteLogEvent.tenant_id == TENANT_ID,
    )
    event = (await db.execute(q)).scalar_one_or_none()
    if event is None:
        return None, None
    job = None
    if event.job_id is not None:
        job = await db.get(Job, event.job_id)
    return event, job
