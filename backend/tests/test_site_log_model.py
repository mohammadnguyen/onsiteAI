"""WP A (A1): model-level tests for the five Site Log capture tables.

Everything here proves a database-enforced invariant from the approved
Revision 3 plan plus founder rulings O1/O2 — not service behaviour
(services are A2). Each constraint-violation case is its own test
because an IntegrityError ends the session's usefulness for that test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    AttachmentState,
    CaptureEligibilityState,
    CaptureEligibilityTransition,
    CaptureStatus,
    Evidence,
    EvidenceMediaType,
    EvidenceStatus,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
    SiteLogEventRevision,
)
from app.models.site_log import (
    ATTACHMENT_STATE_TRANSITIONS,
    FORBIDDEN_AUDIT_DETAIL_KEYS,
    validate_audit_detail,
)


def _event(admin, **kw) -> SiteLogEvent:
    defaults = dict(
        site_log_event_id=uuid.uuid4(),
        author_user_id=admin.user_id,
        capture_client_id=uuid.uuid4(),
    )
    defaults.update(kw)
    return SiteLogEvent(**defaults)


def _revision(event, admin, **kw) -> SiteLogEventRevision:
    defaults = dict(
        revision_id=uuid.uuid4(),
        site_log_event_id=event.site_log_event_id,
        revision_no=1,
        body_text="as captured",
        actor_user_id=admin.user_id,
    )
    defaults.update(kw)
    return SiteLogEventRevision(**defaults)


def _attachment(event, **kw) -> SiteLogEventAttachment:
    defaults = dict(
        attachment_id=uuid.uuid4(),
        site_log_event_id=event.site_log_event_id,
        attachment_client_id=uuid.uuid4(),
        declared_media_type="audio",
    )
    defaults.update(kw)
    return SiteLogEventAttachment(**defaults)


def _transition(event, admin, **kw) -> CaptureEligibilityTransition:
    defaults = dict(
        transition_id=uuid.uuid4(),
        site_log_event_id=event.site_log_event_id,
        transition_no=1,
        from_state=None,
        to_state=CaptureEligibilityState.eligibility_pending_unexposed,
        reason="capture_created",
        actor_user_id=admin.user_id,
    )
    defaults.update(kw)
    return CaptureEligibilityTransition(**defaults)


async def _evidence(db_session, admin) -> Evidence:
    ev = Evidence(
        evidence_id=uuid.uuid4(),
        uploaded_by_user_id=admin.user_id,
        media_type=EvidenceMediaType.audio,
        mime_type="audio/m4a",
        status=EvidenceStatus.stored,
        size_bytes=10,
        sha256="0" * 64,
    )
    db_session.add(ev)
    await db_session.flush()
    return ev


# ---------------------------------------------------------------- roundtrip


@pytest.mark.asyncio
async def test_event_roundtrip_with_revision_attachment_transition(
    db_session, seeded_admin
):
    """An event persists with revision 1, a manifest entry and the N1
    initial eligibility transition, and reloads via relationships."""
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()

    db_session.add_all(
        [
            _revision(
                event,
                seeded_admin,
                occurred_at=datetime(2026, 8, 30, tzinfo=UTC),
            ),
            _attachment(event),
            _transition(event, seeded_admin),
        ]
    )
    await db_session.flush()
    event_id = event.site_log_event_id
    db_session.expire_all()

    loaded = await db_session.get(SiteLogEvent, event_id)
    assert loaded is not None
    assert loaded.capture_status is CaptureStatus.pending_upload
    assert loaded.job_id is None  # unassigned == derived, no stored column
    revisions = (
        await db_session.execute(
            select(SiteLogEventRevision)
            .where(SiteLogEventRevision.site_log_event_id == event_id)
            .order_by(SiteLogEventRevision.revision_no)
        )
    ).scalars().all()
    attachments = (
        await db_session.execute(
            select(SiteLogEventAttachment).where(
                SiteLogEventAttachment.site_log_event_id == event_id
            )
        )
    ).scalars().all()
    assert [r.revision_no for r in revisions] == [1]
    assert revisions[0].occurred_at is not None
    assert len(attachments) == 1
    assert attachments[0].state is AttachmentState.awaiting_upload
    assert attachments[0].evidence_id is None


# ------------------------------------------------------- schema shape (O2)


def test_events_table_single_sources_occurred_at_and_derives_job_state():
    """O2: no occurred_at on events; no stored job_state anywhere."""
    cols = SiteLogEvent.__table__.columns.keys()
    assert "occurred_at" not in cols
    assert "job_state" not in cols
    assert "current_state" not in cols  # eligibility never projected
    assert "occurred_at" in SiteLogEventRevision.__table__.columns


def test_attachment_state_transition_graph_is_closed():
    """O1 requirement 4: the closed graph, pinned so edits are visible."""
    t = ATTACHMENT_STATE_TRANSITIONS
    assert set(t) == set(AttachmentState)
    assert t[AttachmentState.awaiting_upload] == {
        AttachmentState.pending,
        AttachmentState.failed,
    }
    assert t[AttachmentState.pending] == {
        AttachmentState.stored,
        AttachmentState.failed,
    }
    assert t[AttachmentState.stored] == frozenset()  # terminal
    assert t[AttachmentState.failed] == {AttachmentState.pending}  # retry


def test_eligibility_vocabulary():
    """N1 default exists; development_released is a reason, not a state."""
    values = {s.value for s in CaptureEligibilityState}
    assert "eligibility_pending_unexposed" in values
    assert "development_only" in values
    assert "development_released" not in values


# ---------------------------------------------------- audit content rules


def test_audit_detail_validator_rejects_content_keys():
    """O1 requirement 8: audit rows are content-free, at any depth."""
    ok = {"revision_id": "x", "fields": ["body_text"], "size_bytes": 12}
    assert validate_audit_detail(ok) is ok

    with pytest.raises(ValueError):
        validate_audit_detail({"body_text": "raw capture words"})
    with pytest.raises(ValueError):
        validate_audit_detail({"meta": {"nested": {"transcript": "words"}}})
    with pytest.raises(ValueError):
        validate_audit_detail({"items": [{"payload": b"bytes"}]})
    # Vocabulary sanity: the forbidden set stays lower-case (matching is
    # case-insensitive via lowering the candidate key).
    assert all(k == k.lower() for k in FORBIDDEN_AUDIT_DETAIL_KEYS)


@pytest.mark.asyncio
async def test_audit_row_roundtrip(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()

    db_session.add(
        SiteLogEventAuditLog(
            audit_id=uuid.uuid4(),
            site_log_event_id=event.site_log_event_id,
            actor_user_id=seeded_admin.user_id,
            action="created",
            changed_fields=validate_audit_detail(
                {"capture_client_id": str(event.capture_client_id)}
            ),
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_job_with_capture_cannot_be_deleted(db_session, seeded_admin):
    """FK is NO ACTION: a Job referenced by a capture is not an empty Job
    and hard-deleting it is rejected. job_id NULL must mean only "not yet
    confirmed", never "confirmed Job later deleted"."""
    from app.models import Job, JobStatus

    job = Job(
        job_id=uuid.uuid4(),
        job_name="Referenced Job",
        status=JobStatus.active,
        created_by=seeded_admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(_event(seeded_admin, job_id=job.job_id))
    await db_session.flush()

    await db_session.delete(job)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --------------------------------------------------- uniqueness invariants


@pytest.mark.asyncio
async def test_capture_client_id_unique_per_author(db_session, seeded_admin):
    """Offline idempotency: replaying a capture_client_id cannot create a
    second event for the same author (tenant-scoped UNIQUE)."""
    client_id = uuid.uuid4()
    db_session.add(_event(seeded_admin, capture_client_id=client_id))
    await db_session.flush()
    db_session.add(_event(seeded_admin, capture_client_id=client_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_revision_no_unique_per_event(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_revision(event, seeded_admin, revision_no=1))
    await db_session.flush()
    db_session.add(_revision(event, seeded_admin, revision_no=1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_attachment_client_id_unique_per_event(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    dup = uuid.uuid4()
    db_session.add(_attachment(event, attachment_client_id=dup))
    await db_session.flush()
    db_session.add(_attachment(event, attachment_client_id=dup))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_evidence_attaches_to_at_most_one_event(db_session, seeded_admin):
    """Partial UNIQUE on evidence_id: attachment is exclusive."""
    ev = await _evidence(db_session, seeded_admin)
    e1, e2 = _event(seeded_admin), _event(seeded_admin)
    db_session.add_all([e1, e2])
    await db_session.flush()
    db_session.add(
        _attachment(e1, evidence_id=ev.evidence_id, state=AttachmentState.stored)
    )
    await db_session.flush()
    db_session.add(
        _attachment(e2, evidence_id=ev.evidence_id, state=AttachmentState.stored)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_transition_no_unique_per_event(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_transition(event, seeded_admin, transition_no=1))
    await db_session.flush()
    db_session.add(
        _transition(
            event,
            seeded_admin,
            transition_no=1,
            from_state=None,
            to_state=CaptureEligibilityState.development_only,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ------------------------------------------------------- CHECK invariants


@pytest.mark.asyncio
async def test_withdrawal_requires_reason(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        _revision(event, seeded_admin, withdrawn=True, reason=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_correction_requires_reason(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        _revision(event, seeded_admin, revision_no=2, reason=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_revision_no_must_be_positive(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_revision(event, seeded_admin, revision_no=0, reason="r"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_stored_attachment_requires_evidence(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        _attachment(event, state=AttachmentState.stored, evidence_id=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_declared_media_type_closed_list(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_attachment(event, declared_media_type="video"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_first_transition_must_have_null_from_state(
    db_session, seeded_admin
):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        _transition(
            event,
            seeded_admin,
            transition_no=1,
            from_state=CaptureEligibilityState.eligibility_pending_unexposed,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_later_transition_must_have_from_state(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_transition(event, seeded_admin, transition_no=1))
    await db_session.flush()
    db_session.add(
        _transition(
            event,
            seeded_admin,
            transition_no=2,
            from_state=None,
            to_state=CaptureEligibilityState.development_only,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ------------------------------------------------------ A1b attempt counter


@pytest.mark.asyncio
async def test_upload_attempt_no_defaults_to_zero(db_session, seeded_admin):
    """A1b: column exists, NOT NULL, defaults to 0 (no attempt acquired)."""
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    att = _attachment(event)
    db_session.add(att)
    await db_session.flush()
    assert att.upload_attempt_no == 0


@pytest.mark.asyncio
async def test_upload_attempt_no_accepts_increasing_values(
    db_session, seeded_admin
):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    for n in (0, 1, 5):
        db_session.add(_attachment(event, upload_attempt_no=n))
    await db_session.flush()


@pytest.mark.asyncio
async def test_upload_attempt_no_rejects_negative(db_session, seeded_admin):
    event = _event(seeded_admin)
    db_session.add(event)
    await db_session.flush()
    db_session.add(_attachment(event, upload_attempt_no=-1))
    with pytest.raises(IntegrityError):
        await db_session.flush()
