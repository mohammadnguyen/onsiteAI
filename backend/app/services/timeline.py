"""Job Timeline business logic (PR 4 — service layer only).

HTTP-agnostic; raises the domain exceptions defined at the top of this
module. The HTTP layer (``app/api/timeline.py``, PR 5) is the only
intended caller and maps exceptions onto status codes:

* ``JobNotFoundForTimeline`` / ``TimelineItemNotFound`` /
  ``ChecklistItemNotFound`` → 404
* ``TimelinePermissionDenied`` → 403
* ``TimelineValidationError`` → 422
* ``InvalidTimelineCursor`` → 400

Hard rules this module is written against (PR 2/PR 3 review outcomes):

* **Never ``session.get()`` on a soft-deletable entity.** The global
  soft-delete filter is a query-level WHERE; ``Session.get`` can serve
  a soft-deleted object straight from the identity map without running
  it. Every read here goes through ``select().where(pk == ...)``.
* **Relationship loads are eager-only** (``selectinload``): this async
  codebase has no ``AsyncAttrs``, so lazy access raises
  ``MissingGreenlet`` — and eager loads are the path the soft-delete
  criteria propagates into.
* **Access control lives here, not in the router.** Job-level access
  is validated first in every function (:func:`_ensure_job_access` is
  the single seam where a future per-user job ACL lands — V1 is
  single-tenant, so "user can access job" currently means the job
  exists; timeline items are team-visible per the permission matrix).
  Write operations additionally require creator-or-admin; issue
  ``closed`` transitions are admin-only.
* **Every mutation writes one ``timeline_audit_log`` row in the same
  transaction** (action = ``create`` / ``update`` / ``soft_delete`` /
  ``status_change``; ``detail`` carries the coerced field diff). If the
  audit write fails, the flush fails with it and the caller's rollback
  discards the main change — the log can never silently lag reality.
  No-op updates write no audit row (mirrors ``update_job``).
"""

from __future__ import annotations

import base64
import enum
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job
from app.models.timeline import (
    IssueStatus,
    JobChecklistItem,
    TimelineAttachment,
    TimelineAuditLog,
    TimelineItem,
    TimelineItemType,
)
from app.models.user import User, UserRole
from app.schemas.timeline import TimelineItemCreate, TimelineItemUpdate


class JobNotFoundForTimeline(Exception):
    """Raised when a ``job_id`` doesn't resolve to a persisted job."""

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found")


class TimelineItemNotFound(Exception):
    """Raised when an item id doesn't resolve to a live (non-deleted) row."""

    def __init__(self, item_id: uuid.UUID):
        self.item_id = item_id
        super().__init__(f"Timeline item {item_id} not found")


class ChecklistItemNotFound(Exception):
    """Raised when ``(job_id, checklist_item_id)`` doesn't resolve.

    Deliberately raised both when the id doesn't exist and when it
    belongs to a different job — same no-leak rationale as
    :class:`app.services.jobs.BudgetNotFound`.
    """

    def __init__(self, checklist_item_id: uuid.UUID):
        self.checklist_item_id = checklist_item_id
        super().__init__(f"Checklist item {checklist_item_id} not found")


class TimelinePermissionDenied(Exception):
    """Raised when the caller lacks write/transition rights (403)."""

    def __init__(self, detail: str = "Permission denied"):
        self.detail = detail
        super().__init__(detail)


class TimelineValidationError(Exception):
    """Raised on save-time validation errors (422)."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class InvalidTimelineCursor(Exception):
    """Raised when a list-pagination cursor fails to decode (400)."""

    def __init__(self, detail: str = "Invalid cursor"):
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def _owns(item: TimelineItem, user: User) -> bool:
    return item.created_by == user.user_id


async def _ensure_job_access(
    db: AsyncSession, job_id: uuid.UUID, user: User
) -> None:
    """Validate that ``user`` may access ``job_id``.

    This is the single enforcement seam for job-level access. V1 is
    single-tenant with team-visible timelines (per the permission
    matrix: every active internal user can read every job's timeline),
    so today the check is existence — an unknown/foreign ``job_id``
    raises :class:`JobNotFoundForTimeline` (404; no information leak
    about which ids exist elsewhere). When a per-user job ACL ships,
    it lands here and every timeline function inherits it.

    Column-only select on purpose: loading the ``Job`` entity would
    cascade its ``lazy="selectin"`` relationship loads (aliases +
    category budgets) — two extra statements per timeline call that
    an existence check doesn't need.
    """
    found = (
        await db.execute(select(Job.job_id).where(Job.job_id == job_id))
    ).scalar_one_or_none()
    if found is None:
        raise JobNotFoundForTimeline(job_id)


async def _get_item(
    db: AsyncSession,
    item_id: uuid.UUID,
    *,
    with_attachments: bool = False,
) -> TimelineItem:
    """Fetch one live timeline item via ``select`` (NEVER ``session.get``).

    The global soft-delete filter applies, so a soft-deleted item
    raises :class:`TimelineItemNotFound` exactly like a missing one.
    """
    stmt = select(TimelineItem).where(
        TimelineItem.timeline_item_id == item_id
    )
    if with_attachments:
        stmt = stmt.options(selectinload(TimelineItem.attachments))
    item = (await db.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise TimelineItemNotFound(item_id)
    return item


async def _stamp_attachment_count(
    db: AsyncSession, item: TimelineItem
) -> None:
    """Stamp the live-attachment count onto a single item.

    Single-row mutation paths (update / status change) return the item
    for serialisation; ``TimelineItemPublic.attachment_count`` would
    otherwise fall back to 0 and under-report. One COUNT per write is
    fine — the no-N+1 rule applies to the list path, which computes
    counts in its own single statement.
    """
    count = (
        await db.execute(
            select(func.count()).where(
                TimelineAttachment.timeline_item_id == item.timeline_item_id,
                TimelineAttachment.deleted_at.is_(None),
            )
        )
    ).scalar()
    item.attachment_count = int(count or 0)


async def _validate_checklist_link(
    db: AsyncSession, job_id: uuid.UUID, checklist_item_id: uuid.UUID
) -> None:
    """A linked checklist item must exist (live) and belong to the same job."""
    row = (
        await db.execute(
            select(JobChecklistItem).where(
                JobChecklistItem.checklist_item_id == checklist_item_id,
                JobChecklistItem.job_id == job_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise TimelineValidationError(
            "checklist_item_id does not resolve to a checklist item on this job"
        )


def _coerce_timeline_audit_value(value: Any) -> Any:
    """JSON-serialisable form for the JSONB audit detail.

    Local to this module by the same rule as ``_coerce_job_audit_value``
    — audit coercion stays module-private, no cross-module dependency
    on another service's private helper.
    """
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write_audit(
    db: AsyncSession,
    *,
    item: TimelineItem,
    action: str,
    actor: User,
    detail: dict[str, Any] | None,
) -> None:
    """Queue one audit row in the caller's transaction (flushed by caller).

    Same-transaction by construction: the row is added to the session
    that holds the main change, so a failed flush aborts both together.
    """
    db.add(
        TimelineAuditLog(
            audit_id=uuid.uuid4(),
            timeline_item_id=item.timeline_item_id,
            job_id=item.job_id,
            action=action,
            actor_user_id=actor.user_id,
            detail=detail,
        )
    )


# Fields a generic update may touch, and whose changes are audited.
# item_type/status are deliberately absent (immutable kind; status flows
# through change_issue_status only).
_UPDATABLE_FIELDS: tuple[str, ...] = (
    "title",
    "body",
    "severity",
    "checklist_item_id",
    "assigned_user_id",
    "requires_evidence",
    "occurred_at",
)


def _encode_cursor(row: TimelineItem) -> str:
    payload = json.dumps(
        {
            "o": row.occurred_at.isoformat(),
            "i": row.timeline_item_id.hex,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return (
            datetime.fromisoformat(payload["o"]),
            uuid.UUID(hex=payload["i"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidTimelineCursor() from exc


# ---------------------------------------------------------------------------
# Timeline items
# ---------------------------------------------------------------------------


async def list_timeline_items(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    current_user: User,
    item_type: TimelineItemType | None = None,
    status: IssueStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[TimelineItem], str | None]:
    """List a job's live timeline items, newest ``occurred_at`` first.

    Team visibility: any user who can access the job sees every item
    (not just their own) — site facts are shared. Keyset pagination on
    ``(occurred_at DESC, timeline_item_id DESC)`` (the id tiebreak makes
    the order total); ``cursor`` is the opaque ``next_cursor`` from the
    previous page.

    ``attachment_count`` is computed in the same statement via a
    grouped LEFT JOIN subquery (never per-row — no N+1) counting live
    attachments only, and stamped onto each returned instance for
    ``TimelineItemPublic``'s non-column field. The subquery filters
    ``deleted_at`` explicitly: the global soft-delete filter targets
    ORM entity selects and is not relied upon inside a plain subquery.

    ``date_from`` / ``date_to`` are aware datetimes compared against
    ``occurred_at`` (inclusive); the router (PR 5) owns query-param
    parsing.
    """
    await _ensure_job_access(db, job_id, current_user)

    att_counts = (
        select(
            TimelineAttachment.timeline_item_id.label("item_id"),
            func.count().label("cnt"),
        )
        .where(TimelineAttachment.deleted_at.is_(None))
        .group_by(TimelineAttachment.timeline_item_id)
        .subquery()
    )

    stmt = (
        select(TimelineItem, func.coalesce(att_counts.c.cnt, 0))
        .outerjoin(
            att_counts,
            att_counts.c.item_id == TimelineItem.timeline_item_id,
        )
        .where(TimelineItem.job_id == job_id)
        .order_by(
            TimelineItem.occurred_at.desc(),
            TimelineItem.timeline_item_id.desc(),
        )
    )

    if item_type is not None:
        stmt = stmt.where(TimelineItem.item_type == item_type)
    if status is not None:
        stmt = stmt.where(TimelineItem.status == status)
    if date_from is not None:
        stmt = stmt.where(TimelineItem.occurred_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(TimelineItem.occurred_at <= date_to)

    if cursor is not None:
        last_occurred, last_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(TimelineItem.occurred_at, TimelineItem.timeline_item_id)
            < (last_occurred, last_id)
        )

    stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1][0])

    items: list[TimelineItem] = []
    for item, count in rows:
        item.attachment_count = int(count)
        items.append(item)
    return items, next_cursor


async def get_timeline_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    current_user: User,
) -> TimelineItem:
    """Fetch one live item with attachments eager-loaded (detail view).

    Read access is team-wide: the caller only needs access to the
    item's job (validated via the seam). Soft-deleted attachments are
    excluded by criteria propagation into the eager load.
    """
    item = await _get_item(db, item_id, with_attachments=True)
    await _ensure_job_access(db, item.job_id, current_user)
    item.attachment_count = len(item.attachments)
    return item


async def create_timeline_item(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    current_user: User,
    payload: TimelineItemCreate,
) -> TimelineItem:
    """Insert a timeline item on ``job_id`` owned by ``current_user``.

    The schema has already enforced the issue contract (title required,
    status defaulted ``open``, born-``closed`` rejected, non-issue
    rejects status). Here: job access, checklist-link validity, and the
    ``create`` audit row (same transaction).
    """
    await _ensure_job_access(db, job_id, current_user)
    if payload.checklist_item_id is not None:
        await _validate_checklist_link(db, job_id, payload.checklist_item_id)

    item = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=job_id,
        item_type=payload.item_type,
        title=payload.title,
        body=payload.body,
        status=payload.status,
        severity=payload.severity,
        checklist_item_id=payload.checklist_item_id,
        assigned_user_id=payload.assigned_user_id,
        requires_evidence=payload.requires_evidence,
        occurred_at=payload.occurred_at,
        created_by=current_user.user_id,
    )
    db.add(item)

    created_snapshot = {
        f: _coerce_timeline_audit_value(getattr(item, f))
        for f in ("item_type", *_UPDATABLE_FIELDS)
        if getattr(item, f) is not None
    }
    if item.status is not None:
        created_snapshot["status"] = _coerce_timeline_audit_value(item.status)
    _write_audit(
        db,
        item=item,
        action="create",
        actor=current_user,
        detail={"created": created_snapshot},
    )
    await db.flush()
    item.attachment_count = 0
    return item


async def update_timeline_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    current_user: User,
    payload: TimelineItemUpdate,
) -> TimelineItem:
    """Partial update (conditional-spread: ``exclude_unset``).

    Only the creator or an admin may edit. Omitted fields are left
    alone; an explicit ``null`` clears a nullable column — except
    ``title`` on an issue, which is rejected (the PR 3 service
    obligation: the schema cannot see the row's type and the DB CHECK
    covers only ``status``, so this is the invariant's only backstop).

    Writes one ``update`` audit row carrying the changed-field diff;
    a no-op patch writes none.
    """
    item = await _get_item(db, item_id)
    await _ensure_job_access(db, item.job_id, current_user)
    if not (_is_admin(current_user) or _owns(item, current_user)):
        raise TimelinePermissionDenied("Not your timeline item")

    data = payload.model_dump(exclude_unset=True)

    if (
        item.item_type is TimelineItemType.issue
        and "title" in data
        and data["title"] is None
    ):
        raise TimelineValidationError(
            "an issue requires a title; title cannot be cleared on an issue"
        )
    if data.get("checklist_item_id") is not None:
        await _validate_checklist_link(
            db, item.job_id, data["checklist_item_id"]
        )

    pre = {f: getattr(item, f) for f in _UPDATABLE_FIELDS}
    for field, value in data.items():
        setattr(item, field, value)

    changed: dict[str, dict[str, Any]] = {}
    for f in _UPDATABLE_FIELDS:
        if pre[f] != getattr(item, f):
            changed[f] = {
                "old": _coerce_timeline_audit_value(pre[f]),
                "new": _coerce_timeline_audit_value(getattr(item, f)),
            }
    if changed:
        _write_audit(
            db,
            item=item,
            action="update",
            actor=current_user,
            detail=changed,
        )
    await db.flush()
    await _stamp_attachment_count(db, item)
    return item


async def soft_delete_timeline_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    current_user: User,
) -> None:
    """Soft-delete (set ``deleted_at``). Creator or admin only.

    A second delete raises :class:`TimelineItemNotFound` (the filtered
    select no longer sees the row) — deliberately non-silent, same as
    ``delete_category_budget``. The audit row records the timestamp and
    survives the deletion (no hard FK on ``timeline_item_id``).
    """
    item = await _get_item(db, item_id)
    await _ensure_job_access(db, item.job_id, current_user)
    if not (_is_admin(current_user) or _owns(item, current_user)):
        raise TimelinePermissionDenied("Not your timeline item")

    item.deleted_at = datetime.now(UTC)
    _write_audit(
        db,
        item=item,
        action="soft_delete",
        actor=current_user,
        detail={
            "deleted_at": _coerce_timeline_audit_value(item.deleted_at)
        },
    )
    await db.flush()


# Legal issue-status transitions. Key: (current, new) → admin_only.
# open→closed and closed→open are absent: illegal for everyone (close
# must pass through resolved; a closed issue reopens to resolved, not
# straight to open — admin verification stays in the loop both ways).
_ISSUE_TRANSITIONS: dict[tuple[IssueStatus, IssueStatus], bool] = {
    (IssueStatus.open, IssueStatus.resolved): False,
    (IssueStatus.resolved, IssueStatus.open): False,
    (IssueStatus.resolved, IssueStatus.closed): True,
    (IssueStatus.closed, IssueStatus.resolved): True,
}


async def change_issue_status(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    current_user: User,
    new_status: IssueStatus,
) -> TimelineItem:
    """Transition an issue's status through the two-stage sign-off machine.

    Contributors flip ``open ↔ resolved``; entering or leaving
    ``closed`` is admin-only (``resolved→closed`` verify,
    ``closed→resolved`` reopen). ``open→closed`` and ``closed→open``
    are invalid for every role. Requesting the current status is an
    idempotent no-op (returns the item, writes no audit row) so
    weak-network retries are safe.

    Writes one ``status_change`` audit row on actual transitions.
    """
    item = await _get_item(db, item_id)
    await _ensure_job_access(db, item.job_id, current_user)

    if item.item_type is not TimelineItemType.issue:
        raise TimelineValidationError(
            "status transitions are only valid for item_type='issue'"
        )

    current = item.status
    if current == new_status:
        await _stamp_attachment_count(db, item)
        return item  # idempotent retry; no audit noise

    admin_only = _ISSUE_TRANSITIONS.get((current, new_status))
    if admin_only is None:
        raise TimelineValidationError(
            f"illegal status transition {current.value} -> {new_status.value}"
        )
    if admin_only and not _is_admin(current_user):
        raise TimelinePermissionDenied(
            "only an admin can move an issue into or out of 'closed'"
        )

    item.status = new_status
    _write_audit(
        db,
        item=item,
        action="status_change",
        actor=current_user,
        detail={
            "status": {
                "old": _coerce_timeline_audit_value(current),
                "new": _coerce_timeline_audit_value(new_status),
            }
        },
    )
    await db.flush()
    await _stamp_attachment_count(db, item)
    return item


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


async def list_checklist_items(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    current_user: User,
) -> list[JobChecklistItem]:
    """Return the job's live checklist, ``sort_order`` then creation order."""
    await _ensure_job_access(db, job_id, current_user)
    stmt = (
        select(JobChecklistItem)
        .where(JobChecklistItem.job_id == job_id)
        .order_by(
            JobChecklistItem.sort_order.asc(),
            JobChecklistItem.created_at.asc(),
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def toggle_checklist_item(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    checklist_item_id: uuid.UUID,
    current_user: User,
    is_done: bool,
) -> JobChecklistItem:
    """Set a checklist item's done state (explicit target, idempotent).

    ``(job_id, checklist_item_id)`` is validated atomically — an id
    belonging to a different job raises :class:`ChecklistItemNotFound`
    (no-leak). Marking done stamps ``done_at``/``done_by``; un-marking
    clears both. Re-sending the current state is a no-op that preserves
    the original ``done_at``/``done_by`` (weak-network retries must not
    rewrite attribution).

    No ``timeline_audit_log`` row: the audit table's shape is
    timeline-item-centric and the PR 4 audit contract covers item
    mutations only. Checklist auditing, if needed, is a Phase 2 call.
    """
    await _ensure_job_access(db, job_id, current_user)
    row = (
        await db.execute(
            select(JobChecklistItem).where(
                JobChecklistItem.checklist_item_id == checklist_item_id,
                JobChecklistItem.job_id == job_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ChecklistItemNotFound(checklist_item_id)

    if row.is_done == is_done:
        return row  # idempotent retry; keep original attribution

    row.is_done = is_done
    row.done_at = datetime.now(UTC) if is_done else None
    row.done_by = current_user.user_id if is_done else None
    await db.flush()
    return row
