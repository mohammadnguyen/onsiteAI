"""PR 4 — Timeline service-layer tests.

One test (or parametrised group) per hard acceptance criterion:

1. Zero ``session.get()`` in the service source (soft-delete safety).
2. Job-level access enforced in every function (unknown ids rejected).
3. Write permissions: creator-or-admin edits/deletes; ``closed``
   transitions admin-only; illegal transitions rejected for all roles.
4. ``title: null`` on an issue rejected at update (PR 3 obligation).
5. Audit rows for create/update/soft_delete/status_change, written in
   the same transaction (atomicity proven via a failing audit FK
   rolling back the main change); no-op mutations write none.
6. Eager-only relationship loading (detail attachments accessible
   without lazy IO; soft-deleted attachments excluded).
7. ``exclude_unset`` conditional-spread semantics on update.
8. ``attachment_count`` computed in the list statement itself —
   statement count is constant regardless of item count (no N+1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

import app.services.timeline as svc
from app.models import (
    IssueStatus,
    Job,
    JobChecklistItem,
    JobStatus,
    TimelineAttachment,
    TimelineAuditLog,
    TimelineItem,
    TimelineItemType,
    User,
    UserRole,
)
from app.models.user import LanguageCode
from app.schemas.timeline import TimelineItemCreate, TimelineItemUpdate

_T0 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
async def _mk_job(db, admin, *, name: str = "Kelly House") -> Job:
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db.add(job)
    await db.flush()
    return job


def _note_payload(*, minutes: int = 0, **overrides) -> TimelineItemCreate:
    fields: dict = {
        "item_type": TimelineItemType.daily_note,
        "body": "Slab poured.",
        "occurred_at": _T0 + timedelta(minutes=minutes),
    }
    fields.update(overrides)
    return TimelineItemCreate(**fields)


def _issue_payload(*, minutes: int = 0, **overrides) -> TimelineItemCreate:
    fields: dict = {
        "item_type": TimelineItemType.issue,
        "title": "Leaking pipe",
        "occurred_at": _T0 + timedelta(minutes=minutes),
    }
    fields.update(overrides)
    return TimelineItemCreate(**fields)


async def _mk_item(db, job, user, payload=None) -> TimelineItem:
    return await svc.create_timeline_item(
        db, job_id=job.job_id, current_user=user, payload=payload or _note_payload()
    )


def _mk_attachment(item, user, *, deleted: bool = False) -> TimelineAttachment:
    return TimelineAttachment(
        attachment_id=uuid.uuid4(),
        timeline_item_id=item.timeline_item_id,
        storage_key=f"k/{uuid.uuid4().hex}.jpg",
        content_type="image/jpeg",
        created_by=user.user_id,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


async def _mk_checklist(db, job, *, label="flood test", sort_order=0) -> JobChecklistItem:
    row = JobChecklistItem(
        checklist_item_id=uuid.uuid4(),
        job_id=job.job_id,
        label=label,
        sort_order=sort_order,
    )
    db.add(row)
    await db.flush()
    return row


async def _audit_rows(db, item_id) -> list[TimelineAuditLog]:
    stmt = (
        select(TimelineAuditLog)
        .where(TimelineAuditLog.timeline_item_id == item_id)
        .order_by(TimelineAuditLog.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


@pytest.fixture
async def second_contributor(db_session) -> User:
    """A second contributor for the non-creator write-permission matrix."""
    from app.core.security import hash_password

    user = User(
        user_id=uuid.uuid4(),
        full_name="Second Contributor",
        email="second-contributor@example.com",
        password_hash=hash_password("x"),
        role=UserRole.contributor,
        language_preference=LanguageCode.en,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


# --------------------------------------------------------------------------- #
# AC 1 — no session.get() on soft-deletable entities                          #
# --------------------------------------------------------------------------- #
def test_service_source_has_no_session_get():
    """The service must never read via Session.get (identity-map bypass
    of the soft-delete filter, PR 2 finding). AST-level guarantee: no
    ``.get(...)`` / ``.get_one(...)`` call on any session-named object
    anywhere in the module (docstrings that *mention* the rule don't
    count; dict ``.get`` on other names stays legal)."""
    import ast

    source = Path(svc.__file__).read_text(encoding="utf-8")
    offenders: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "get_one")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("db", "session", "db_session")
        ):
            offenders.append(node.lineno)
    assert offenders == [], f"session.get-style calls at lines {offenders}"


# --------------------------------------------------------------------------- #
# AC 2 — job-level access enforced in every function                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unknown_job_rejected_everywhere(db_session, seeded_contributor):
    ghost_job = uuid.uuid4()
    with pytest.raises(svc.JobNotFoundForTimeline):
        await svc.list_timeline_items(
            db_session, job_id=ghost_job, current_user=seeded_contributor
        )
    with pytest.raises(svc.JobNotFoundForTimeline):
        await svc.create_timeline_item(
            db_session,
            job_id=ghost_job,
            current_user=seeded_contributor,
            payload=_note_payload(),
        )
    with pytest.raises(svc.JobNotFoundForTimeline):
        await svc.list_checklist_items(
            db_session, job_id=ghost_job, current_user=seeded_contributor
        )
    with pytest.raises(svc.JobNotFoundForTimeline):
        await svc.toggle_checklist_item(
            db_session,
            job_id=ghost_job,
            checklist_item_id=uuid.uuid4(),
            current_user=seeded_contributor,
            is_done=True,
        )


@pytest.mark.asyncio
async def test_unknown_item_rejected_everywhere(db_session, seeded_admin):
    ghost = uuid.uuid4()
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.get_timeline_item(
            db_session, item_id=ghost, current_user=seeded_admin
        )
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.update_timeline_item(
            db_session,
            item_id=ghost,
            current_user=seeded_admin,
            payload=TimelineItemUpdate(title="x"),
        )
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.soft_delete_timeline_item(
            db_session, item_id=ghost, current_user=seeded_admin
        )
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.change_issue_status(
            db_session,
            item_id=ghost,
            current_user=seeded_admin,
            new_status=IssueStatus.resolved,
        )


@pytest.mark.asyncio
async def test_team_visibility_within_job(
    db_session, seeded_admin, seeded_contributor
):
    """A contributor sees items created by others on the same job."""
    job = await _mk_job(db_session, seeded_admin)
    admin_item = await _mk_item(db_session, job, seeded_admin)

    items, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_contributor
    )
    assert [i.timeline_item_id for i in items] == [admin_item.timeline_item_id]

    got = await svc.get_timeline_item(
        db_session,
        item_id=admin_item.timeline_item_id,
        current_user=seeded_contributor,
    )
    assert got.timeline_item_id == admin_item.timeline_item_id


# --------------------------------------------------------------------------- #
# AC 3 — write permissions (creator-or-admin; closed = admin-only)            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_creator_or_admin_only(
    db_session, seeded_admin, seeded_contributor, second_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _mk_item(db_session, job, seeded_contributor)

    with pytest.raises(svc.TimelinePermissionDenied):
        await svc.update_timeline_item(
            db_session,
            item_id=item.timeline_item_id,
            current_user=second_contributor,
            payload=TimelineItemUpdate(body="hijack"),
        )

    updated = await svc.update_timeline_item(
        db_session,
        item_id=item.timeline_item_id,
        current_user=seeded_contributor,
        payload=TimelineItemUpdate(body="mine"),
    )
    assert updated.body == "mine"

    updated = await svc.update_timeline_item(
        db_session,
        item_id=item.timeline_item_id,
        current_user=seeded_admin,
        payload=TimelineItemUpdate(body="admin override"),
    )
    assert updated.body == "admin override"


@pytest.mark.asyncio
async def test_soft_delete_creator_or_admin_only(
    db_session, seeded_admin, seeded_contributor, second_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    mine = await _mk_item(db_session, job, seeded_contributor)
    other = await _mk_item(db_session, job, seeded_contributor, _note_payload(minutes=1))

    with pytest.raises(svc.TimelinePermissionDenied):
        await svc.soft_delete_timeline_item(
            db_session,
            item_id=mine.timeline_item_id,
            current_user=second_contributor,
        )

    await svc.soft_delete_timeline_item(
        db_session, item_id=mine.timeline_item_id, current_user=seeded_contributor
    )
    await svc.soft_delete_timeline_item(
        db_session, item_id=other.timeline_item_id, current_user=seeded_admin
    )

    items, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin
    )
    assert items == []

    # Second delete: the filtered select no longer sees the row -> 404.
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.soft_delete_timeline_item(
            db_session, item_id=mine.timeline_item_id, current_user=seeded_admin
        )


@pytest.mark.asyncio
async def test_status_open_resolved_flip_any_team_member(
    db_session, seeded_admin, seeded_contributor, second_contributor
):
    """open<->resolved is not creator-gated: any team member resolves."""
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload())

    resolved = await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=second_contributor,
        new_status=IssueStatus.resolved,
    )
    assert resolved.status is IssueStatus.resolved

    reopened = await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=second_contributor,
        new_status=IssueStatus.open,
    )
    assert reopened.status is IssueStatus.open


@pytest.mark.asyncio
async def test_closed_transitions_admin_only(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload())
    await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_contributor,
        new_status=IssueStatus.resolved,
    )

    # resolved -> closed: contributor denied, admin allowed.
    with pytest.raises(svc.TimelinePermissionDenied):
        await svc.change_issue_status(
            db_session,
            item_id=issue.timeline_item_id,
            current_user=seeded_contributor,
            new_status=IssueStatus.closed,
        )
    closed = await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_admin,
        new_status=IssueStatus.closed,
    )
    assert closed.status is IssueStatus.closed

    # closed -> resolved (reopen out of closed): contributor denied, admin ok.
    with pytest.raises(svc.TimelinePermissionDenied):
        await svc.change_issue_status(
            db_session,
            item_id=issue.timeline_item_id,
            current_user=seeded_contributor,
            new_status=IssueStatus.resolved,
        )
    reopened = await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_admin,
        new_status=IssueStatus.resolved,
    )
    assert reopened.status is IssueStatus.resolved


@pytest.mark.asyncio
async def test_illegal_transitions_rejected_for_everyone(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload())

    # open -> closed direct: invalid even for admin (must pass resolved).
    with pytest.raises(svc.TimelineValidationError, match="illegal status transition"):
        await svc.change_issue_status(
            db_session,
            item_id=issue.timeline_item_id,
            current_user=seeded_admin,
            new_status=IssueStatus.closed,
        )

    # Drive to closed properly, then closed -> open: invalid even for admin.
    await svc.change_issue_status(
        db_session, item_id=issue.timeline_item_id,
        current_user=seeded_admin, new_status=IssueStatus.resolved,
    )
    await svc.change_issue_status(
        db_session, item_id=issue.timeline_item_id,
        current_user=seeded_admin, new_status=IssueStatus.closed,
    )
    with pytest.raises(svc.TimelineValidationError, match="illegal status transition"):
        await svc.change_issue_status(
            db_session,
            item_id=issue.timeline_item_id,
            current_user=seeded_admin,
            new_status=IssueStatus.open,
        )


@pytest.mark.asyncio
async def test_status_change_on_non_issue_rejected(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    note = await _mk_item(db_session, job, seeded_admin)
    with pytest.raises(svc.TimelineValidationError, match="only valid for item_type='issue'"):
        await svc.change_issue_status(
            db_session,
            item_id=note.timeline_item_id,
            current_user=seeded_admin,
            new_status=IssueStatus.resolved,
        )


# --------------------------------------------------------------------------- #
# AC 4 — title:null on an issue rejected (PR 3 service obligation)            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_title_null_on_issue_rejected(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload())

    with pytest.raises(svc.TimelineValidationError, match="issue requires a title"):
        await svc.update_timeline_item(
            db_session,
            item_id=issue.timeline_item_id,
            current_user=seeded_contributor,
            payload=TimelineItemUpdate(title=None),
        )

    # Replacing (not clearing) the title stays legal.
    updated = await svc.update_timeline_item(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_contributor,
        payload=TimelineItemUpdate(title="Burst pipe"),
    )
    assert updated.title == "Burst pipe"


@pytest.mark.asyncio
async def test_update_title_null_on_note_clears(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    note = await _mk_item(
        db_session, job, seeded_admin, _note_payload(title="Morning note")
    )
    updated = await svc.update_timeline_item(
        db_session,
        item_id=note.timeline_item_id,
        current_user=seeded_admin,
        payload=TimelineItemUpdate(title=None),
    )
    assert updated.title is None


# --------------------------------------------------------------------------- #
# AC 5 — audit rows + same-transaction atomicity                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_audit_rows_for_each_mutation(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload())

    rows = await _audit_rows(db_session, issue.timeline_item_id)
    assert [r.action for r in rows] == ["create"]
    assert rows[0].actor_user_id == seeded_contributor.user_id
    assert rows[0].job_id == job.job_id
    assert rows[0].detail["created"]["item_type"] == "issue"
    assert rows[0].detail["created"]["status"] == "open"

    await svc.update_timeline_item(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_contributor,
        payload=TimelineItemUpdate(title="Burst pipe"),
    )
    rows = await _audit_rows(db_session, issue.timeline_item_id)
    assert [r.action for r in rows] == ["create", "update"]
    assert rows[1].detail == {
        "title": {"old": "Leaking pipe", "new": "Burst pipe"}
    }

    await svc.change_issue_status(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_contributor,
        new_status=IssueStatus.resolved,
    )
    rows = await _audit_rows(db_session, issue.timeline_item_id)
    assert [r.action for r in rows] == ["create", "update", "status_change"]
    assert rows[2].detail == {"status": {"old": "open", "new": "resolved"}}

    await svc.soft_delete_timeline_item(
        db_session,
        item_id=issue.timeline_item_id,
        current_user=seeded_contributor,
    )
    rows = await _audit_rows(db_session, issue.timeline_item_id)
    assert [r.action for r in rows] == [
        "create", "update", "status_change", "soft_delete",
    ]
    assert rows[3].detail["deleted_at"] is not None


@pytest.mark.asyncio
async def test_noop_mutations_write_no_audit(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_admin, _issue_payload())

    # Empty patch and same-value patch: no audit row.
    await svc.update_timeline_item(
        db_session, item_id=issue.timeline_item_id,
        current_user=seeded_admin, payload=TimelineItemUpdate(),
    )
    await svc.update_timeline_item(
        db_session, item_id=issue.timeline_item_id,
        current_user=seeded_admin, payload=TimelineItemUpdate(title="Leaking pipe"),
    )
    # Idempotent same-status request: no audit row.
    await svc.change_issue_status(
        db_session, item_id=issue.timeline_item_id,
        current_user=seeded_admin, new_status=IssueStatus.open,
    )

    rows = await _audit_rows(db_session, issue.timeline_item_id)
    assert [r.action for r in rows] == ["create"]


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_main_change(
    db_session, seeded_admin, seeded_contributor
):
    """Atomicity: item UPDATE and audit INSERT share one flush. A ghost
    admin whose user_id violates the audit FK must abort BOTH."""
    from app.core.security import hash_password

    job = await _mk_job(db_session, seeded_admin)
    item = await _mk_item(db_session, job, seeded_contributor)
    # Capture plain values now: the savepoint rollback below expires the
    # instance, and expired-attribute refresh is lazy IO (MissingGreenlet
    # in async) — the assertions must not touch the stale object.
    item_id = item.timeline_item_id
    original_body = item.body

    ghost_admin = User(  # transient: never added to the session
        user_id=uuid.uuid4(),
        full_name="Ghost",
        email="ghost@example.com",
        password_hash=hash_password("x"),
        role=UserRole.admin,
        language_preference=LanguageCode.en,
        is_active=True,
    )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await svc.update_timeline_item(
                db_session,
                item_id=item_id,
                current_user=ghost_admin,
                payload=TimelineItemUpdate(body="should not persist"),
            )

    db_session.expunge_all()  # discard the expired instance; force SQL reads
    reloaded = (
        await db_session.execute(
            select(TimelineItem).where(
                TimelineItem.timeline_item_id == item_id
            )
        )
    ).scalar_one()
    assert reloaded.body == original_body
    rows = await _audit_rows(db_session, item_id)
    assert [r.action for r in rows] == ["create"]


# --------------------------------------------------------------------------- #
# AC 6 — eager-only relationship loading                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_detail_eager_loads_live_attachments(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    item = await _mk_item(db_session, job, seeded_contributor)
    live = _mk_attachment(item, seeded_contributor)
    dead = _mk_attachment(item, seeded_contributor, deleted=True)
    db_session.add_all([live, dead])
    await db_session.flush()
    db_session.expunge_all()

    got = await svc.get_timeline_item(
        db_session, item_id=item.timeline_item_id, current_user=seeded_admin
    )
    # Synchronous access — eager-loaded, no lazy IO (MissingGreenlet-safe),
    # and the soft-deleted attachment is filtered out by propagation.
    assert [a.attachment_id for a in got.attachments] == [live.attachment_id]
    assert got.attachment_count == 1


# --------------------------------------------------------------------------- #
# AC 7 — exclude_unset conditional-spread semantics                           #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_omitted_vs_explicit_null(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    checklist = await _mk_checklist(db_session, job)
    item = await _mk_item(
        db_session, job, seeded_admin,
        _note_payload(title="Titled", checklist_item_id=checklist.checklist_item_id),
    )

    # Omitted fields untouched: only body changes.
    updated = await svc.update_timeline_item(
        db_session, item_id=item.timeline_item_id,
        current_user=seeded_admin, payload=TimelineItemUpdate(body="new body"),
    )
    assert updated.title == "Titled"
    assert updated.checklist_item_id == checklist.checklist_item_id

    # Explicit null clears the nullable link.
    updated = await svc.update_timeline_item(
        db_session, item_id=item.timeline_item_id,
        current_user=seeded_admin,
        payload=TimelineItemUpdate(checklist_item_id=None),
    )
    assert updated.checklist_item_id is None
    assert updated.body == "new body"  # untouched this round


@pytest.mark.asyncio
async def test_checklist_link_must_belong_to_same_job(
    db_session, seeded_admin
):
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    foreign = await _mk_checklist(db_session, job_b)

    with pytest.raises(svc.TimelineValidationError, match="checklist item on this job"):
        await svc.create_timeline_item(
            db_session, job_id=job_a.job_id, current_user=seeded_admin,
            payload=_note_payload(checklist_item_id=foreign.checklist_item_id),
        )

    item = await _mk_item(db_session, job_a, seeded_admin)
    with pytest.raises(svc.TimelineValidationError, match="checklist item on this job"):
        await svc.update_timeline_item(
            db_session, item_id=item.timeline_item_id,
            current_user=seeded_admin,
            payload=TimelineItemUpdate(checklist_item_id=foreign.checklist_item_id),
        )


# --------------------------------------------------------------------------- #
# AC 8 — list: no N+1, attachment_count, filters, pagination                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_statement_count_constant(db_session, seeded_admin):
    """The list path emits exactly 2 statements (job-access check + the
    single list SELECT with its count subquery) regardless of item count."""
    job = await _mk_job(db_session, seeded_admin)

    async def _measure() -> int:
        statements: list[str] = []

        def _cb(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_conn = db_session.sync_session.bind
        event.listen(sync_conn, "before_cursor_execute", _cb)
        try:
            await svc.list_timeline_items(
                db_session, job_id=job.job_id, current_user=seeded_admin
            )
        finally:
            event.remove(sync_conn, "before_cursor_execute", _cb)
        return len(statements)

    baseline = await _measure()  # empty job

    for n in range(6):
        item = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=n))
        db_session.add(_mk_attachment(item, seeded_admin))
    await db_session.flush()

    with_items = await _measure()
    assert baseline == with_items == 2


@pytest.mark.asyncio
async def test_list_attachment_counts_exclude_deleted(
    db_session, seeded_admin
):
    job = await _mk_job(db_session, seeded_admin)
    with_photos = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=1))
    bare = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=0))
    db_session.add_all([
        _mk_attachment(with_photos, seeded_admin),
        _mk_attachment(with_photos, seeded_admin),
        _mk_attachment(with_photos, seeded_admin, deleted=True),
    ])
    await db_session.flush()

    items, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin
    )
    by_id = {i.timeline_item_id: i.attachment_count for i in items}
    assert by_id[with_photos.timeline_item_id] == 2
    assert by_id[bare.timeline_item_id] == 0


@pytest.mark.asyncio
async def test_list_ordering_filters_and_soft_delete(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    note_old = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=0))
    issue = await _mk_item(db_session, job, seeded_contributor, _issue_payload(minutes=2))
    note_new = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=4))
    gone = await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=6))
    await svc.soft_delete_timeline_item(
        db_session, item_id=gone.timeline_item_id, current_user=seeded_admin
    )

    items, next_cursor = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin
    )
    assert [i.timeline_item_id for i in items] == [
        note_new.timeline_item_id,      # newest occurred_at first
        issue.timeline_item_id,
        note_old.timeline_item_id,      # soft-deleted item absent
    ]
    assert next_cursor is None

    only_issues, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin,
        item_type=TimelineItemType.issue,
    )
    assert [i.timeline_item_id for i in only_issues] == [issue.timeline_item_id]

    open_only, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin,
        status=IssueStatus.open,
    )
    assert [i.timeline_item_id for i in open_only] == [issue.timeline_item_id]

    windowed, _ = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin,
        date_from=_T0 + timedelta(minutes=1),
        date_to=_T0 + timedelta(minutes=3),
    )
    assert [i.timeline_item_id for i in windowed] == [issue.timeline_item_id]


@pytest.mark.asyncio
async def test_list_keyset_pagination_walks_without_overlap(
    db_session, seeded_admin
):
    job = await _mk_job(db_session, seeded_admin)
    created = [
        await _mk_item(db_session, job, seeded_admin, _note_payload(minutes=n))
        for n in range(5)
    ]
    expected = [i.timeline_item_id for i in reversed(created)]  # DESC

    page1, cursor1 = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin, limit=2
    )
    assert cursor1 is not None
    page2, cursor2 = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin,
        limit=2, cursor=cursor1,
    )
    assert cursor2 is not None
    page3, cursor3 = await svc.list_timeline_items(
        db_session, job_id=job.job_id, current_user=seeded_admin,
        limit=2, cursor=cursor2,
    )
    assert cursor3 is None

    walked = [i.timeline_item_id for i in (*page1, *page2, *page3)]
    assert walked == expected


@pytest.mark.asyncio
async def test_list_invalid_cursor_rejected(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    with pytest.raises(svc.InvalidTimelineCursor):
        await svc.list_timeline_items(
            db_session, job_id=job.job_id, current_user=seeded_admin,
            cursor="not-a-cursor",
        )


# --------------------------------------------------------------------------- #
# Soft-deleted items are dead to every mutation path                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_soft_deleted_item_unreachable(db_session, seeded_admin):
    job = await _mk_job(db_session, seeded_admin)
    issue = await _mk_item(db_session, job, seeded_admin, _issue_payload())
    await svc.soft_delete_timeline_item(
        db_session, item_id=issue.timeline_item_id, current_user=seeded_admin
    )
    db_session.expunge_all()  # cold identity map: force SQL reads

    with pytest.raises(svc.TimelineItemNotFound):
        await svc.get_timeline_item(
            db_session, item_id=issue.timeline_item_id, current_user=seeded_admin
        )
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.update_timeline_item(
            db_session, item_id=issue.timeline_item_id,
            current_user=seeded_admin, payload=TimelineItemUpdate(title="x"),
        )
    with pytest.raises(svc.TimelineItemNotFound):
        await svc.change_issue_status(
            db_session, item_id=issue.timeline_item_id,
            current_user=seeded_admin, new_status=IssueStatus.resolved,
        )


# --------------------------------------------------------------------------- #
# Checklist                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_checklist_list_ordering(db_session, seeded_admin, seeded_contributor):
    job = await _mk_job(db_session, seeded_admin)
    second = await _mk_checklist(db_session, job, label="second", sort_order=2)
    first = await _mk_checklist(db_session, job, label="first", sort_order=1)

    rows = await svc.list_checklist_items(
        db_session, job_id=job.job_id, current_user=seeded_contributor
    )
    assert [r.checklist_item_id for r in rows] == [
        first.checklist_item_id,
        second.checklist_item_id,
    ]


@pytest.mark.asyncio
async def test_checklist_toggle_stamps_and_clears(
    db_session, seeded_admin, seeded_contributor
):
    job = await _mk_job(db_session, seeded_admin)
    row = await _mk_checklist(db_session, job)

    done = await svc.toggle_checklist_item(
        db_session, job_id=job.job_id,
        checklist_item_id=row.checklist_item_id,
        current_user=seeded_contributor, is_done=True,
    )
    assert done.is_done is True
    assert done.done_at is not None
    assert done.done_by == seeded_contributor.user_id
    first_done_at = done.done_at

    # Idempotent retry (even by another user) keeps original attribution.
    again = await svc.toggle_checklist_item(
        db_session, job_id=job.job_id,
        checklist_item_id=row.checklist_item_id,
        current_user=seeded_admin, is_done=True,
    )
    assert again.done_at == first_done_at
    assert again.done_by == seeded_contributor.user_id

    undone = await svc.toggle_checklist_item(
        db_session, job_id=job.job_id,
        checklist_item_id=row.checklist_item_id,
        current_user=seeded_admin, is_done=False,
    )
    assert undone.is_done is False
    assert undone.done_at is None
    assert undone.done_by is None


@pytest.mark.asyncio
async def test_checklist_toggle_wrong_job_no_leak(db_session, seeded_admin):
    job_a = await _mk_job(db_session, seeded_admin, name="Job A")
    job_b = await _mk_job(db_session, seeded_admin, name="Job B")
    row_b = await _mk_checklist(db_session, job_b)

    # Real id, wrong job: identical error to a nonexistent id.
    with pytest.raises(svc.ChecklistItemNotFound):
        await svc.toggle_checklist_item(
            db_session, job_id=job_a.job_id,
            checklist_item_id=row_b.checklist_item_id,
            current_user=seeded_admin, is_done=True,
        )
    with pytest.raises(svc.ChecklistItemNotFound):
        await svc.toggle_checklist_item(
            db_session, job_id=job_a.job_id,
            checklist_item_id=uuid.uuid4(),
            current_user=seeded_admin, is_done=True,
        )
