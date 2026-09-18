"""Upload-phase failure bookkeeping in the two upload services.

Fail-first module for the PR #14 follow-up (recorded base for this round:
``8ea7ad1``, i.e. WP-S-core before this fix). Since WP-S(4) the storage
adapters re-raise a chunk-SOURCE exception unchanged, so an ordinary
non-cap failure (an I/O error reading the spooled request body) no longer
arrives as ``EvidenceStorageError`` and — before the fix — reached the API
with the row left ``pending``: 409 on every Site Log retry until an admin
reset, and a legacy Evidence row stuck ``pending`` with no ``failed``
audit. The module imports only names that exist on that base, so it runs
against a scratch checkout of it: the bookkeeping tests fail at their
state assertions, or at the ``SiteLogUploadInProgress`` that the stuck
pending row causes on the next attempt (never at an import or fixture
error), while the tests marked ``# regression`` pass on both versions.

Covered: both services; exception type preserved; local adapter and the
recording S3 fake; the adoption reads (``exists`` / ``open``) inside the
failure boundary; a late failure from a superseded attempt; cancellation
and post-upload failures NOT recorded as upload failures; a bookkeeping
commit failure, whose persistence outcome the service must report as
unconfirmed rather than claiming either ``failed`` or ``pending``.

The rollback harness used here cannot observe a row that survived a real
commit, so the durable outcome of BOTH bookkeeping-fault shapes (fault
before the commit, and fault after a commit that landed) is pinned in
test_upload_failure_persistence.py against a scratch database read over an
independent connection.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models import (
    AttachmentState,
    Evidence,
    EvidenceAuditLog,
    EvidenceStatus,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
)
from app.services import evidence as evidence_service
from app.services import site_log as svc
from app.services.evidence_storage import (
    EvidenceStorageError,
    LocalEvidenceStorage,
    ObjectAlreadyExists,
    ObjectNotFound,
    StoragePermanentError,
    StorageTransientError,
    make_object_key,
)
from app.services.site_log import upload as svc_upload
from tests.support.s3_fake import client_error, fake_s3_storage

MAX_BYTES = 1024 * 1024


# ------------------------------------------------------------------ helpers


async def _chunks(payload: bytes = b"bytes"):
    yield payload


async def _source_os_error():
    """A chunk source that dies the way a spooled-body read dies."""
    yield b"first"
    raise OSError(5, "Input/output error")


@pytest.fixture
def storage(tmp_path):
    return LocalEvidenceStorage(tmp_path)


async def _declare_one(db, storage, factory, user):
    """One capture with one ordinary attachment; returns (event_id, cid)."""
    cid = uuid.uuid4()
    res = await svc.declare_capture(
        db, storage, factory,
        user=user,
        capture_client_id=uuid.uuid4(),
        job_id=None,
        occurred_at=None,
        internal_location=None,
        body_text=None,
        attachments=[{
            "attachment_client_id": cid,
            "declared_media_type": "audio",
            "declared_size_bytes": None,
        }],
        max_bytes=MAX_BYTES,
    )
    return res.view.event.site_log_event_id, cid


async def _row(db, event_id, cid) -> SiteLogEventAttachment:
    q = (
        select(SiteLogEventAttachment)
        .where(
            SiteLogEventAttachment.site_log_event_id == event_id,
            SiteLogEventAttachment.attachment_client_id == cid,
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(q)).scalar_one()


async def _ev_status(db, evidence_id) -> EvidenceStatus:
    q = select(Evidence.status).where(Evidence.evidence_id == evidence_id)
    return (await db.execute(q)).scalar_one()


async def _ev_storage_key(db, evidence_id) -> str | None:
    q = select(Evidence.storage_key).where(Evidence.evidence_id == evidence_id)
    return (await db.execute(q)).scalar_one()


async def _ev_audit_actions(db, evidence_id) -> list[str]:
    q = (
        select(EvidenceAuditLog.action)
        .where(EvidenceAuditLog.evidence_id == evidence_id)
        .order_by(EvidenceAuditLog.created_at, EvidenceAuditLog.audit_id)
    )
    return list((await db.execute(q)).scalars().all())


async def _event_audits(db, event_id) -> int:
    q = select(func.count()).select_from(SiteLogEventAuditLog).where(
        SiteLogEventAuditLog.site_log_event_id == event_id
    )
    return (await db.execute(q)).scalar_one()


async def _assert_failed_recorded(db, event_id, cid, *, attempt_no=1, error_class=None):
    """The DURABLE state every failed upload attempt must leave behind.

    The rollback first is what makes this a durability assertion: the test
    harness runs the whole test inside one transaction, so a write that was
    only flushed (never committed by the service) is still visible to a
    plain read-back. Rolling back the caller's session discards exactly the
    uncommitted part, so what survives is what the service committed.
    """
    await db.rollback()
    row = await _row(db, event_id, cid)
    assert row.state is AttachmentState.failed
    assert row.upload_attempt_no == attempt_no
    assert await _ev_status(db, row.evidence_id) is EvidenceStatus.failed
    assert await _ev_audit_actions(db, row.evidence_id) == ["uploaded", "failed"]
    if error_class is not None:
        details = (
            await db.execute(
                select(EvidenceAuditLog.detail).where(
                    EvidenceAuditLog.evidence_id == row.evidence_id
                )
            )
        ).scalars().all()
        assert any(d.get("error_class") == error_class for d in details), details
    return row


class _CollidingStorage:
    """Reports a collision at this attempt's own key, then answers the
    adoption reads as configured. Stands in for the post-database-restore
    anomaly that is the only remaining producer of ``ObjectAlreadyExists``
    on the Site Log path under attempt-scoped keys."""

    backend_name = "local"

    def __init__(
        self, *, exists_error=None, exists=True, open_error=None,
        open_raises_sync=False, payload=b"adopted",
    ):
        self.key = None  # set from the real evidence_id at put time
        self._open_raises_sync = open_raises_sync
        self._exists_error = exists_error
        self._exists = exists
        self._open_error = open_error
        self._payload = payload

    async def put(self, evidence_id, chunks, *, attempt_no=None):
        async for _ in chunks:
            pass
        # The collision is at THIS attempt's own key (the only shape FD1
        # leaves reachable), so the adopted key stays inside the row's own
        # evidence_id prefix.
        self.key = make_object_key(str(evidence_id), "de" * 32, attempt_no)
        raise ObjectAlreadyExists(self.key)

    async def exists(self, key):
        if self._exists_error is not None:
            raise self._exists_error
        return self._exists

    def open(self, key):
        open_error = self._open_error
        payload = self._payload
        if open_error is not None and self._open_raises_sync:
            # LocalEvidenceStorage.open raises before any iteration.
            raise open_error

        async def _iter():
            if open_error is not None:
                raise open_error
            yield payload

        return _iter()


# ------------------------------------------- Site Log: ordinary source failure


async def test_site_log_source_failure_records_failed_and_keeps_type(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Local adapter: the OSError reaches the caller unchanged AND the
    attempt is durably failed (before the fix the row stayed pending)."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)

    with pytest.raises(OSError) as info:
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_source_os_error(), max_bytes=MAX_BYTES,
        )
    assert not isinstance(info.value, EvidenceStorageError)  # type preserved
    assert info.value.errno == 5

    await _assert_failed_recorded(db_session, eid, cid, error_class="OSError")


async def test_site_log_source_failure_records_failed_on_s3_backend(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Same guarantee through the recording S3 fake (staging/production
    backend), whose put aborts its own multipart upload and re-raises."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    s3, state = fake_s3_storage()

    with pytest.raises(OSError):
        await svc.upload_attachment(
            db_session, s3, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_source_os_error(), max_bytes=MAX_BYTES,
        )

    row = await _assert_failed_recorded(db_session, eid, cid)
    assert await _ev_storage_key(db_session, row.evidence_id) is None
    assert len(state.aborts) == 1  # this attempt's staging was cleaned up


async def test_site_log_source_failure_allows_immediate_retry(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """The point of the bookkeeping: the field user can retry at once
    instead of waiting for an admin reset."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    with pytest.raises(OSError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_source_os_error(), max_bytes=MAX_BYTES,
        )

    result = await svc.upload_attachment(
        db_session, storage, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(b"retry ok"), max_bytes=MAX_BYTES,
    )
    assert result.attachment.state is AttachmentState.stored
    assert result.attachment.upload_attempt_no == 2
    assert result.evidence.status is EvidenceStatus.stored
    assert result.evidence.storage_key.endswith(".a2")


# ------------------------------------------------- Site Log: adoption branch


async def test_adoption_exists_failure_records_failed(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """A classified error from ``exists`` inside the collision handler is
    an upload failure, not an escape hatch (before the fix it bypassed the
    failed transition and left the row pending)."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    colliding = _CollidingStorage(exists_error=StorageTransientError("head 503"))

    with pytest.raises(StorageTransientError):
        await svc.upload_attachment(
            db_session, colliding, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(), max_bytes=MAX_BYTES,
        )

    await _assert_failed_recorded(db_session, eid, cid)


async def test_adoption_read_failure_records_failed(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Same for a failure while re-reading the existing object."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    colliding = _CollidingStorage(open_error=StoragePermanentError("get 403"))

    with pytest.raises(StoragePermanentError):
        await svc.upload_attachment(
            db_session, colliding, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(), max_bytes=MAX_BYTES,
        )

    await _assert_failed_recorded(db_session, eid, cid)


async def test_adoption_read_failure_raised_synchronously_records_failed(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """The local adapter raises from ``open`` itself, before iteration."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    colliding = _CollidingStorage(
        open_error=ObjectNotFound("evidence/x/gone"), open_raises_sync=True
    )

    with pytest.raises(ObjectNotFound):
        await svc.upload_attachment(
            db_session, colliding, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(), max_bytes=MAX_BYTES,
        )

    await _assert_failed_recorded(db_session, eid, cid)


async def test_collision_without_object_still_records_failed(  # regression
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Unchanged behaviour: a collision whose object is absent is a
    storage error, and it is still recorded."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    colliding = _CollidingStorage(exists=False)

    with pytest.raises(EvidenceStorageError) as info:
        await svc.upload_attachment(
            db_session, colliding, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(), max_bytes=MAX_BYTES,
        )
    assert "collision without object" in str(info.value)
    assert isinstance(info.value.__cause__, ObjectAlreadyExists)

    await _assert_failed_recorded(db_session, eid, cid)


async def test_successful_adoption_behaviour_unchanged(  # regression
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Regression: a collision whose object reads back still completes the
    attempt from the adopted object (``_adopt`` is NOT removed here)."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    colliding = _CollidingStorage(payload=b"adopted bytes")

    result = await svc.upload_attachment(
        db_session, colliding, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(), max_bytes=MAX_BYTES,
    )
    assert result.attachment.state is AttachmentState.stored
    assert result.evidence.status is EvidenceStatus.stored
    assert result.evidence.storage_key == colliding.key
    assert colliding.key.startswith(f"evidence/{result.evidence.evidence_id}/")
    assert result.evidence.size_bytes == len(b"adopted bytes")


# --------------------------------------------- attempt scoping and boundaries


async def test_late_failure_of_superseded_attempt_leaves_new_attempt_intact(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """A failure surfacing late for attempt 1 must not write anything once
    attempt 2 owns the row — the attempt check inside the reused failure
    transaction is what guarantees it."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    with pytest.raises(OSError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_source_os_error(), max_bytes=MAX_BYTES,
        )
    ok = await svc.upload_attachment(
        db_session, storage, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(b"attempt two"), max_bytes=MAX_BYTES,
    )
    assert ok.attachment.upload_attempt_no == 2
    audits_before = await _event_audits(db_session, eid)
    key_before = ok.evidence.storage_key

    # The final step exercises the new boundary directly: on the base the
    # test never gets here (the stuck pending row blocks attempt 2).
    stale = OSError(5, "late attempt-1 failure")
    await svc_upload._fail_upload_attempt(
        db_session, actor=seeded_admin, event_id=eid,
        attachment_id=ok.attachment.attachment_id, attempt_no=1, error=stale,
    )

    row = await _row(db_session, eid, cid)
    assert row.state is AttachmentState.stored and row.upload_attempt_no == 2
    assert await _ev_status(db_session, row.evidence_id) is EvidenceStatus.stored
    assert await _ev_storage_key(db_session, row.evidence_id) == key_before
    assert await _event_audits(db_session, eid) == audits_before  # no-write return
    # No diagnosis attached: a no-write return is a definite outcome, not an
    # unconfirmed one.
    assert not getattr(stale, "__notes__", [])


async def test_post_upload_failure_is_not_recorded_as_upload_failure(  # regression
    db_session, seeded_admin, storage, site_log_session_factory, monkeypatch
):
    """Requirement boundary: a completion/commit failure after the bytes
    are stored must not be turned into a failed upload attempt."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)

    async def boom(*args, **kwargs):
        raise RuntimeError("txn b exploded")

    monkeypatch.setattr(svc_upload, "complete_attachment", boom)
    with pytest.raises(RuntimeError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(b"stored fine"), max_bytes=MAX_BYTES,
        )

    row = await _row(db_session, eid, cid)
    assert row.state is AttachmentState.pending  # untouched by the upload path
    assert await _ev_status(db_session, row.evidence_id) is EvidenceStatus.pending
    assert await _ev_audit_actions(db_session, row.evidence_id) == ["uploaded"]


async def test_cancellation_leaves_the_row_pending(  # regression
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Cancellation keeps the established recovery rule: no failed
    transition, the row waits for the admin reset path."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    started = asyncio.Event()

    async def stalled():
        yield b"first"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=stalled(), max_bytes=MAX_BYTES,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = await _row(db_session, eid, cid)
    assert row.state is AttachmentState.pending
    assert row.upload_attempt_no == 1
    assert await _ev_audit_actions(db_session, row.evidence_id) == ["uploaded"]


async def test_bookkeeping_failure_reports_persistence_as_unconfirmed(
    db_session, seeded_admin, storage, site_log_session_factory, monkeypatch, caplog
):
    """When the bookkeeping commit fails, the service may claim neither a
    durable ``failed`` nor a surviving ``pending``: it reports the
    persistence outcome as unconfirmed and the original exception wins.

    This case injects the fault before the commit reaches the database, so
    the row here is in fact still pending — asserted below AFTER discarding
    the uncommitted work. test_upload_failure_persistence.py pins both fault
    shapes against a real committing database."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)

    # Let the REAL _fail_attachment run (so manifest + Evidence + both
    # audit rows are actually mutated and flushed) and fail only at its
    # final commit — the exact case the contract describes.
    real_commit = type(db_session).commit
    armed = {"on": False}

    async def flaky_commit(self):
        if armed["on"]:
            armed["on"] = False
            raise RuntimeError("failure commit lost the connection")
        return await real_commit(self)

    real_fail = svc_upload._fail_attachment

    async def arming_fail(*args, **kwargs):
        armed["on"] = True
        try:
            return await real_fail(*args, **kwargs)
        finally:
            armed["on"] = False

    monkeypatch.setattr(type(db_session), "commit", flaky_commit)
    monkeypatch.setattr(svc_upload, "_fail_attachment", arming_fail)
    with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_source_os_error(), max_bytes=MAX_BYTES,
        )

    notes = getattr(info.value, "__notes__", [])
    assert any("persistence UNCONFIRMED" in note for note in notes)
    assert any("RuntimeError" in note for note in notes)
    assert any("persistence UNCONFIRMED" in r.getMessage() for r in caplog.records)
    # Nothing was committed: after discarding the uncommitted work the row
    # is still pending, exactly as the contract claims.
    monkeypatch.undo()
    await db_session.rollback()
    row = await _row(db_session, eid, cid)
    assert row.state is AttachmentState.pending
    assert row.upload_attempt_no == 1
    assert await _ev_status(db_session, row.evidence_id) is EvidenceStatus.pending
    assert await _ev_audit_actions(db_session, row.evidence_id) == ["uploaded"]


# ------------------------------------------------- cap / storage regressions


async def test_size_cap_and_storage_error_reasons_unchanged(  # regression
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Established handling for the cap and the adapter classes is
    untouched: both still record failed with their own reason."""
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)
    with pytest.raises(svc.SiteLogTooLarge):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(b"x" * 32), max_bytes=8,
        )
    row = await _assert_failed_recorded(db_session, eid, cid)

    # A real ADAPTER failure (the S3 client raises), not a source exception
    # that happens to carry a storage type.
    s3, _ = fake_s3_storage(errors={"create_multipart_upload": client_error("503", 503)})
    with pytest.raises(EvidenceStorageError):
        await svc.upload_attachment(
            db_session, s3, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_chunks(b"adapter down"), max_bytes=MAX_BYTES,
        )
    await db_session.rollback()
    row = await _row(db_session, eid, cid)
    assert row.state is AttachmentState.failed and row.upload_attempt_no == 2

    reasons = (
        await db_session.execute(
            select(SiteLogEventAuditLog.changed_fields)
            .where(SiteLogEventAuditLog.site_log_event_id == eid)
            .order_by(SiteLogEventAuditLog.created_at, SiteLogEventAuditLog.audit_id)
        )
    ).scalars().all()
    recorded = [d.get("reason") for d in reasons if d.get("to") == "failed"]
    assert recorded == ["size_cap", "storage_error"]


async def test_source_failure_audit_is_content_free(
    db_session, seeded_admin, storage, site_log_session_factory
):
    eid, cid = await _declare_one(db_session, storage, site_log_session_factory, seeded_admin)

    async def marked_source():
        yield b"SECRET-CAPTURE-CONTENT-MARKER"
        raise OSError(5, "C:/private/path/SECRET-CAPTURE-CONTENT-MARKER.tmp")

    with pytest.raises(OSError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=marked_source(), max_bytes=MAX_BYTES,
        )
    row = await _row(db_session, eid, cid)
    details = (
        await db_session.execute(
            select(SiteLogEventAuditLog.changed_fields).where(
                SiteLogEventAuditLog.site_log_event_id == eid
            )
        )
    ).scalars().all()
    ev_details = (
        await db_session.execute(
            select(EvidenceAuditLog.detail).where(
                EvidenceAuditLog.evidence_id == row.evidence_id
            )
        )
    ).scalars().all()
    blob = str(details) + str(ev_details)
    assert "SECRET-CAPTURE-CONTENT-MARKER" not in blob
    assert "private" not in blob
    assert "internal_error" in blob


# --------------------------------------------------------- legacy /evidence


async def test_legacy_evidence_source_failure_records_failed(
    db_session, seeded_admin, storage
):
    """The legacy upload path records the failure too (before the fix the
    row stayed pending with only the 'uploaded' audit)."""
    uploader_id = seeded_admin.user_id  # read before any rollback expires it
    with pytest.raises(OSError) as info:
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin,
            chunks=_source_os_error(),
            mime_type="audio/m4a",
            occurred_at=None,
            original_filename=None,
            job_id=None,
            max_bytes=MAX_BYTES,
        )
    assert not isinstance(info.value, EvidenceStorageError)
    assert info.value.errno == 5

    await db_session.rollback()  # only committed state survives (see TV-1)
    ids = (
        await db_session.execute(
            select(Evidence.evidence_id).where(
                Evidence.uploaded_by_user_id == uploader_id
            )
        )
    ).scalars().all()
    assert len(ids) == 1
    assert await _ev_status(db_session, ids[0]) is EvidenceStatus.failed
    assert await _ev_storage_key(db_session, ids[0]) is None
    assert await _ev_audit_actions(db_session, ids[0]) == ["uploaded", "failed"]


async def test_legacy_evidence_source_failure_audit_is_content_free(
    db_session, seeded_admin, storage
):
    async def marked_source():
        yield b"SECRET-CAPTURE-CONTENT-MARKER"
        raise OSError(5, "C:/private/path/SECRET-CAPTURE-CONTENT-MARKER.tmp")

    with pytest.raises(OSError):
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin,
            chunks=marked_source(),
            mime_type="audio/m4a",
            occurred_at=None,
            original_filename=None,
            job_id=None,
            max_bytes=MAX_BYTES,
        )
    details = (await db_session.execute(select(EvidenceAuditLog.detail))).scalars().all()
    blob = str(details)
    assert "SECRET-CAPTURE-CONTENT-MARKER" not in blob
    assert "private" not in blob
    assert "OSError" in blob and "internal_error" in blob


async def test_legacy_evidence_cap_and_storage_paths_unchanged(  # regression
    db_session, seeded_admin, storage
):
    with pytest.raises(evidence_service.EvidenceTooLarge):
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin, chunks=_chunks(b"x" * 32), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=8,
        )

    async def adapter_failure():
        yield b"partial"
        raise ObjectNotFound("evidence/x/missing")

    with pytest.raises(EvidenceStorageError):
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin, chunks=adapter_failure(), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=MAX_BYTES,
        )

    details = (await db_session.execute(select(EvidenceAuditLog.detail))).scalars().all()
    reasons = sorted(d["reason"] for d in details if d.get("reason"))
    assert reasons == ["size_cap_exceeded", "storage_error"]


async def test_legacy_evidence_bookkeeping_failure_reports_unconfirmed(
    db_session, seeded_admin, storage, monkeypatch, caplog
):
    uploader_id = seeded_admin.user_id
    real_commit = type(db_session).commit
    calls = {"n": 0}

    async def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] == 2:  # Txn 1 commits; the failure commit does not.
            # The handler has already mutated evidence.status and added the
            # audit row, so this is a commit failure after the writes exist
            # in the session — the rollback below must discard them.
            raise RuntimeError("failure commit lost the connection")
        return await real_commit(self)

    monkeypatch.setattr(type(db_session), "commit", flaky_commit)
    with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin, chunks=_source_os_error(), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=MAX_BYTES,
        )
    notes = getattr(info.value, "__notes__", [])
    assert any("persistence UNCONFIRMED" in note for note in notes)
    assert any("persistence UNCONFIRMED" in r.getMessage() for r in caplog.records)
    monkeypatch.undo()
    await db_session.rollback()
    ids = (
        await db_session.execute(
            select(Evidence.evidence_id).where(
                Evidence.uploaded_by_user_id == uploader_id
            )
        )
    ).scalars().all()
    assert len(ids) == 1
    assert await _ev_status(db_session, ids[0]) is EvidenceStatus.pending
    assert await _ev_audit_actions(db_session, ids[0]) == ["uploaded"]


async def test_legacy_evidence_source_failure_records_failed_on_s3_backend(
    db_session, seeded_admin
):
    """The legacy path over the staging/production backend: same durable
    record, and this attempt's staging upload is aborted."""
    uploader_id = seeded_admin.user_id
    s3, state = fake_s3_storage()
    with pytest.raises(OSError):
        await evidence_service.create_evidence(
            db_session, s3,
            uploader=seeded_admin, chunks=_source_os_error(), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=MAX_BYTES,
        )
    await db_session.rollback()
    ids = (
        await db_session.execute(
            select(Evidence.evidence_id).where(
                Evidence.uploaded_by_user_id == uploader_id
            )
        )
    ).scalars().all()
    assert len(ids) == 1
    assert await _ev_status(db_session, ids[0]) is EvidenceStatus.failed
    assert await _ev_audit_actions(db_session, ids[0]) == ["uploaded", "failed"]
    assert len(state.aborts) == 1


async def test_legacy_evidence_cancellation_leaves_the_row_pending(  # regression
    db_session, seeded_admin, storage
):
    uploader_id = seeded_admin.user_id
    started = asyncio.Event()

    async def stalled():
        yield b"first"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin, chunks=stalled(), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=MAX_BYTES,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await db_session.rollback()
    ids = (
        await db_session.execute(
            select(Evidence.evidence_id).where(
                Evidence.uploaded_by_user_id == uploader_id
            )
        )
    ).scalars().all()
    assert len(ids) == 1
    assert await _ev_status(db_session, ids[0]) is EvidenceStatus.pending
    assert await _ev_audit_actions(db_session, ids[0]) == ["uploaded"]


async def test_api_source_failure_is_still_an_unhandled_500(  # regression
    db_session, admin_token
):
    """The approved contract: an ordinary source failure keeps its status —
    500, not the 502 an adapter failure gets or the 409 a stuck row gave."""
    from httpx import ASGITransport, AsyncClient

    from app.api.evidence import get_evidence_storage
    from app.database import get_db
    from app.main import app

    class _SourceKillingStorage:
        backend_name = "local"

        async def put(self, evidence_id, chunks, *, attempt_no=None):
            async for _ in chunks:
                break
            raise OSError(5, "Input/output error")

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_evidence_storage] = _SourceKillingStorage
    # raise_app_exceptions=False so the unhandled exception is observed as
    # the 500 a real server returns, instead of being re-raised in the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as api:
            resp = await api.post(
                "/evidence",
                files={"file": ("capture.m4a", b"bytes", "audio/m4a")},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    finally:
        app.dependency_overrides.pop(get_evidence_storage, None)
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 500


async def test_legacy_bookkeeping_flush_failure_does_not_mask_the_source_error(
    db_session, seeded_admin, storage, monkeypatch, caplog
):
    """A bookkeeping commit that fails INSIDE its flush rolls back and
    expires the Evidence instance. The handler must still report the
    original upload exception, so it may not read an ORM attribute of that
    expired instance while building its diagnosis."""
    uploader_id = seeded_admin.user_id
    real_commit = type(db_session).commit
    calls = {"n": 0}

    async def expiring_commit(self):
        calls["n"] += 1
        if calls["n"] == 2:
            # What a failed flush leaves behind: the instance is expired, so
            # any later attribute read would need IO and would raise.
            self.expire_all()
            raise RuntimeError("flush failed inside commit")
        return await real_commit(self)

    monkeypatch.setattr(type(db_session), "commit", expiring_commit)
    with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
        await evidence_service.create_evidence(
            db_session, storage,
            uploader=seeded_admin, chunks=_source_os_error(), mime_type="audio/m4a",
            occurred_at=None, original_filename=None, job_id=None, max_bytes=MAX_BYTES,
        )
    assert info.value.errno == 5  # the original exception, not a masking one
    assert any(
        "persistence UNCONFIRMED" in n for n in getattr(info.value, "__notes__", [])
    )
    assert any("persistence UNCONFIRMED" in r.getMessage() for r in caplog.records)

    monkeypatch.undo()
    await db_session.rollback()
    ids = (
        await db_session.execute(
            select(Evidence.evidence_id).where(
                Evidence.uploaded_by_user_id == uploader_id
            )
        )
    ).scalars().all()
    assert len(ids) == 1
    assert await _ev_status(db_session, ids[0]) is EvidenceStatus.pending
