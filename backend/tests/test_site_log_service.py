"""WP A A2a — service-level tests for the capture lifecycle.

Everything here drives ``app.services.site_log`` directly against the
sanctioned Postgres test DB (rollback harness), with the local storage
adapter rooted in ``tmp_path``. Synthetic identifiers and bytes only.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttachmentState,
    CaptureEligibilityTransition,
    CaptureStatus,
    Evidence,
    EvidenceAuditLog,
    EvidenceStatus,
    Job,
    JobStatus,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
    SiteLogEventRevision,
)
from app.services import evidence as evidence_service
from app.services import site_log as svc
from app.services.evidence_storage import (
    EvidenceStorageError,
    LocalEvidenceStorage,
    make_object_key,
)

SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"
MAX_BYTES = 1024 * 1024


async def _chunks(payload: bytes):
    yield payload


async def _exploding():
    yield b"partial"
    raise EvidenceStorageError("disk gone")


def _att(cid=None, media="audio", size=None):
    return {
        "attachment_client_id": cid or uuid.uuid4(),
        "declared_media_type": media,
        "declared_size_bytes": size,
    }


async def _mk_job(db, admin, *, status=JobStatus.active, name="Job"):
    job = Job(job_id=uuid.uuid4(), job_name=name, status=status, created_by=admin.user_id)
    db.add(job)
    await db.flush()
    return job


async def _declare(db, storage, factory, user, **kw):
    params = dict(
        capture_client_id=uuid.uuid4(),
        job_id=None,
        occurred_at=None,
        internal_location=None,
        body_text=None,
        attachments=[],
        max_bytes=MAX_BYTES,
    )
    params.update(kw)
    return await svc.declare_capture(db, storage, factory, user=user, **params)


async def _count(db, model, **where):
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return (await db.execute(q)).scalar_one()


async def _att_row(db, event_id, client_id):
    q = (
        select(SiteLogEventAttachment)
        .where(
            SiteLogEventAttachment.site_log_event_id == event_id,
            SiteLogEventAttachment.attachment_client_id == client_id,
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(q)).scalar_one()


async def _ev_cols(db, evidence_id):
    """(status, sha256, job_id) straight from the DB — bypasses identity map."""
    q = select(Evidence.status, Evidence.sha256, Evidence.job_id).where(
        Evidence.evidence_id == evidence_id
    )
    return (await db.execute(q)).one()


@pytest.fixture
def storage(tmp_path):
    return LocalEvidenceStorage(tmp_path)


# ------------------------------------------------------------- declare


async def test_declare_shape3_external_only_is_atomic(
    db_session, seeded_admin, storage, site_log_session_factory
):
    a1, a2 = _att(media="audio"), _att(media="image", size=10)
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin,
        attachments=[a1, a2],
    )
    assert res.created and not res.inline_failed
    e = res.view.event
    assert e.capture_status is CaptureStatus.pending_upload
    assert res.view.revision.revision_no == 1
    assert res.view.revision.body_text is None
    assert {a.attachment_client_id for a in res.view.attachments} == {
        a1["attachment_client_id"], a2["attachment_client_id"]
    }
    assert all(a.state is AttachmentState.awaiting_upload for a in res.view.attachments)
    assert all(a.upload_attempt_no == 0 for a in res.view.attachments)
    trans = (
        await db_session.execute(
            select(CaptureEligibilityTransition).where(
                CaptureEligibilityTransition.site_log_event_id == e.site_log_event_id
            )
        )
    ).scalars().all()
    assert [(t.transition_no, t.from_state, t.to_state.value) for t in trans] == [
        (1, None, "eligibility_pending_unexposed")
    ]
    audit = (
        await db_session.execute(
            select(SiteLogEventAuditLog).where(
                SiteLogEventAuditLog.site_log_event_id == e.site_log_event_id
            )
        )
    ).scalars().all()
    assert [a.action for a in audit] == ["created"]
    assert re.fullmatch(r"[0-9a-f]{64}", audit[0].changed_fields["declaration_fingerprint"])


async def test_declare_shape1_inline_text_completes_with_exact_bytes(
    db_session, seeded_admin, storage, site_log_session_factory
):
    # NFD sequence + trailing spaces + emoji: must survive byte-for-byte.
    body = "café — fix the ensuite  \U0001F6A7  "
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, body_text=body
    )
    assert res.created and not res.inline_failed
    e = res.view.event
    assert e.capture_status is CaptureStatus.complete
    assert res.view.revision.body_text == body
    assert len(res.view.attachments) == 1
    inline = res.view.attachments[0]
    assert inline.attachment_client_id == svc.inline_attachment_id(e.capture_client_id)
    assert inline.declared_media_type == "text"
    assert inline.state is AttachmentState.stored
    assert inline.upload_attempt_no == 1
    ev = await db_session.get(Evidence, inline.evidence_id)
    assert ev.status is EvidenceStatus.stored and ev.mime_type == svc.INLINE_TEXT_MIME
    stored_bytes = b"".join([c async for c in storage.open(ev.storage_key)])
    assert stored_bytes == body.encode("utf-8")
    import unicodedata

    assert stored_bytes != unicodedata.normalize("NFC", body).encode("utf-8")
    assert ev.size_bytes == len(stored_bytes)


async def test_declare_shape2_text_plus_external(
    db_session, seeded_admin, storage, site_log_session_factory
):
    ext = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin,
        body_text="note", attachments=[ext],
    )
    e = res.view.event
    assert e.capture_status is CaptureStatus.pending_upload
    states = {a.attachment_client_id: a.state for a in res.view.attachments}
    assert states[ext["attachment_client_id"]] is AttachmentState.awaiting_upload
    assert states[svc.inline_attachment_id(e.capture_client_id)] is AttachmentState.stored


async def test_declare_shape4_and_blank_text_rejected(
    db_session, seeded_admin, storage, site_log_session_factory
):
    with pytest.raises(svc.SiteLogValidationError):
        await _declare(db_session, storage, site_log_session_factory, seeded_admin)
    with pytest.raises(svc.SiteLogValidationError):
        await _declare(
            db_session, storage, site_log_session_factory, seeded_admin, body_text="   \n\t"
        )
    assert await _count(db_session, SiteLogEvent) == 0


def test_inline_namespace_pinned_and_deterministic():
    """Changing this constant is a compatibility change, not a refactor."""
    assert uuid.UUID("3f2c1a4e-7b6d-4e0f-9a8c-5d1e2f3a4b6c") == svc.INLINE_TEXT_NAMESPACE
    cid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    assert svc.inline_attachment_id(cid) == uuid.uuid5(svc.INLINE_TEXT_NAMESPACE, str(cid))
    assert svc.inline_attachment_id(cid) == svc.inline_attachment_id(cid)
    assert svc.inline_attachment_id(cid) == uuid.UUID("8b9dfb82-7331-55d6-8880-4e78464d2048")


async def test_inline_id_collision_rejected(
    db_session, seeded_admin, storage, site_log_session_factory
):
    cid = uuid.uuid4()
    with pytest.raises(svc.SiteLogValidationError):
        await _declare(
            db_session, storage, site_log_session_factory, seeded_admin,
            capture_client_id=cid, body_text="x",
            attachments=[_att(cid=svc.inline_attachment_id(cid))],
        )
    assert await _count(db_session, SiteLogEvent) == 0


async def test_replay_identical_returns_existing(
    db_session, seeded_admin, storage, site_log_session_factory
):
    cid, att = uuid.uuid4(), _att()
    kw = dict(capture_client_id=cid, body_text="hello", attachments=[att])
    first = await _declare(db_session, storage, site_log_session_factory, seeded_admin, **kw)
    second = await _declare(db_session, storage, site_log_session_factory, seeded_admin, **kw)
    assert first.created and not second.created
    assert second.view.event.site_log_event_id == first.view.event.site_log_event_id
    assert await _count(db_session, SiteLogEvent) == 1
    assert await _count(db_session, SiteLogEventAttachment) == 2
    assert await _count(db_session, Evidence) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"body_text": "different"},
        {"internal_location": "Roof"},
        {"occurred_at": datetime(2026, 9, 1, tzinfo=UTC)},
        "job",
        "att_media",
        "att_size",
    ],
)
async def test_replay_divergent_declaration_is_rejected(
    db_session, seeded_admin, storage, site_log_session_factory, mutation
):
    cid, att = uuid.uuid4(), _att(media="audio", size=5)
    base = dict(capture_client_id=cid, body_text="hello", attachments=[att])
    await _declare(db_session, storage, site_log_session_factory, seeded_admin, **base)
    kw = dict(base)
    if mutation == "job":
        kw["job_id"] = (await _mk_job(db_session, seeded_admin)).job_id
    elif mutation == "att_media":
        kw["attachments"] = [{**att, "declared_media_type": "image"}]
    elif mutation == "att_size":
        kw["attachments"] = [{**att, "declared_size_bytes": 6}]
    else:
        kw.update(mutation)
    with pytest.raises(svc.SiteLogFingerprintMismatch):
        await _declare(db_session, storage, site_log_session_factory, seeded_admin, **kw)
    assert await _count(db_session, SiteLogEvent) == 1


# -------------------------------------------------------------- upload


class _BindingAssertingStorage(LocalEvidenceStorage):
    """Proves Evidence is governed before bytes: put() sees the manifest
    already pending, bound, attempt 1, Evidence pending with NULL sha."""

    def __init__(self, root, factory, event_id, client_id):
        super().__init__(root)
        self._factory, self._event_id, self._client_id = factory, event_id, client_id
        self.observed = None

    async def put(self, evidence_id, chunks, *, attempt_no=None):
        s = self._factory()
        try:
            att = await _att_row(s, self._event_id, self._client_id)
            ev = await s.get(Evidence, att.evidence_id)
            self.observed = (
                att.state, str(att.evidence_id) == evidence_id,
                att.upload_attempt_no, ev.status, ev.sha256, attempt_no,
            )
        finally:
            await s.close()
        return await super().put(evidence_id, chunks, attempt_no=attempt_no)


async def test_upload_binds_evidence_before_bytes(
    db_session, seeded_admin, tmp_path, site_log_session_factory
):
    plain = LocalEvidenceStorage(tmp_path)
    att = _att()
    res = await _declare(
        db_session, plain, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    storage = _BindingAssertingStorage(tmp_path, site_log_session_factory, eid, cid)
    up = await svc.upload_attachment(
        db_session, storage, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(b"voice bytes"), max_bytes=MAX_BYTES,
    )
    assert storage.observed == (
        AttachmentState.pending, True, 1, EvidenceStatus.pending, None, 1
    )
    assert not up.replay
    assert up.attachment.state is AttachmentState.stored
    assert up.evidence.status is EvidenceStatus.stored
    assert up.evidence.storage_key == make_object_key(
        str(up.evidence.evidence_id), up.evidence.sha256
    )


async def test_retry_reuses_bound_evidence_and_increments_attempt(
    db_session, seeded_admin, storage, site_log_session_factory
):
    att = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    with pytest.raises(EvidenceStorageError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
            chunks=_exploding(), max_bytes=MAX_BYTES,
        )
    row = await _att_row(db_session, eid, cid)
    first_evidence = row.evidence_id
    ev = await db_session.get(Evidence, first_evidence)
    assert (row.state, row.upload_attempt_no) == (AttachmentState.failed, 1)
    assert ev.status is EvidenceStatus.failed and ev.sha256 is None

    up = await svc.upload_attachment(
        db_session, storage, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(b"second try"), max_bytes=MAX_BYTES,
    )
    row = await _att_row(db_session, eid, cid)
    assert row.evidence_id == first_evidence  # same identity across retries
    assert (row.state, row.upload_attempt_no) == (AttachmentState.stored, 2)
    assert up.evidence.status is EvidenceStatus.stored
    assert await _count(db_session, Evidence) == 1  # never a second row

    # Replay after success: nothing written, same evidence.
    again = await svc.upload_attachment(
        db_session, storage, site_log_session_factory, user=seeded_admin,
        event_id=eid, attachment_client_id=cid, mime_type="audio/m4a",
        chunks=_chunks(b"ignored"), max_bytes=MAX_BYTES,
    )
    assert again.replay and again.evidence.evidence_id == first_evidence


async def test_obsolete_attempt_cannot_complete_after_newer_acquisition(
    db_session, seeded_admin, storage, site_log_session_factory
):
    att = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    _, ev, n1, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    row = await _att_row(db_session, eid, cid)
    await svc._fail_attachment(
        db_session, actor=seeded_admin, event_id=eid,
        attachment_id=row.attachment_id, attempt_no=n1, reason="storage_error",
    )
    _, _, n2, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    assert (n1, n2) == (1, 2)
    stored = await storage.put(str(ev.evidence_id), _chunks(b"late"), attempt_no=n1)
    with pytest.raises(svc.SiteLogAttemptSuperseded):
        await svc.complete_attachment(
            site_log_session_factory, actor_id=seeded_admin.user_id, event_id=eid,
            attachment_id=row.attachment_id, attempt_no=n1, stored=stored,
            backend_name=storage.backend_name,
        )
    row = await _att_row(db_session, eid, cid)
    status, sha, _ = await _ev_cols(db_session, row.evidence_id)
    assert (row.state, row.upload_attempt_no) == (AttachmentState.pending, 2)
    assert status is EvidenceStatus.pending and sha is None


async def test_pending_retry_is_409_and_media_mismatch_precedes_acquisition(
    db_session, seeded_admin, storage, site_log_session_factory
):
    att = _att(media="audio")
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    with pytest.raises(svc.SiteLogMediaMismatch):
        await svc.acquire_attachment(
            db_session, user=seeded_admin, event_id=eid,
            attachment_client_id=cid, mime_type="image/png",
        )
    row = await _att_row(db_session, eid, cid)
    assert row.upload_attempt_no == 0 and row.evidence_id is None
    assert await _count(db_session, Evidence) == 0

    await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    with pytest.raises(svc.SiteLogUploadInProgress):
        await svc.acquire_attachment(
            db_session, user=seeded_admin, event_id=eid,
            attachment_client_id=cid, mime_type="audio/m4a",
        )


# ---------------------------------------------------------- admin reset


async def test_reset_rules_and_atomic_coupled_failure(
    db_session, seeded_admin, seeded_contributor, storage, site_log_session_factory
):
    att = _att()
    # Authored by the contributor: readable by author (403 on reset) and
    # admin; unreadable by anyone else while unassigned (404).
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_contributor, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    now = datetime.now(UTC)
    with pytest.raises(svc.SiteLogNothingToReset):
        await svc.reset_attachment(
            db_session, admin=seeded_admin, event_id=eid,
            attachment_client_id=cid, reason="stuck", now=now,
        )
    await svc.acquire_attachment(
        db_session, user=seeded_contributor, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    with pytest.raises(svc.SiteLogForbidden):
        await svc.reset_attachment(
            db_session, admin=seeded_contributor, event_id=eid,
            attachment_client_id=cid, reason="stuck", now=now,
        )
    with pytest.raises(svc.SiteLogNotFound):  # unknown event: 404 even for non-admin
        await svc.reset_attachment(
            db_session, admin=seeded_contributor, event_id=uuid.uuid4(),
            attachment_client_id=cid, reason="stuck", now=now,
        )
    with pytest.raises(svc.SiteLogReasonRequired):
        await svc.reset_attachment(
            db_session, admin=seeded_admin, event_id=eid,
            attachment_client_id=cid, reason="  ", now=now,
        )
    with pytest.raises(svc.SiteLogResetNotEligible):
        await svc.reset_attachment(
            db_session, admin=seeded_admin, event_id=eid,
            attachment_client_id=cid, reason="stuck", now=now,
        )
    att_id = (await _att_row(db_session, eid, cid)).attachment_id
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_id == att_id)
        .values(updated_at=now - timedelta(minutes=14, seconds=59))
    )
    with pytest.raises(svc.SiteLogResetNotEligible):
        await svc.reset_attachment(
            db_session, admin=seeded_admin, event_id=eid,
            attachment_client_id=cid, reason="stuck", now=now,
        )
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_id == att_id)
        .values(updated_at=now - timedelta(minutes=15))
    )
    out = await svc.reset_attachment(
        db_session, admin=seeded_admin, event_id=eid,
        attachment_client_id=cid, reason="stuck after crash", now=now,
    )
    assert out.state is AttachmentState.failed
    status, _, _ = await _ev_cols(db_session, out.evidence_id)
    assert status is EvidenceStatus.failed
    # next retry acquires N+1
    _, _, n, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    assert n == 2


# ---------------------------------------------------- txn B retry policy


class _FakeOrig:
    def __init__(self, sqlstate):
        self.sqlstate = sqlstate


def _dbapi(sqlstate=None, invalidated=False):
    exc = DBAPIError("stmt", None, _FakeOrig(sqlstate), connection_invalidated=invalidated)
    return exc


def test_retry_whitelist():
    assert svc._is_retryable(_dbapi("40001"))
    assert svc._is_retryable(_dbapi("40P01"))
    assert svc._is_retryable(_dbapi("55P03"))
    assert svc._is_retryable(_dbapi(None, invalidated=True))
    assert not svc._is_retryable(_dbapi("23505"))
    assert not svc._is_retryable(_dbapi("08006"))
    assert not svc._is_retryable(IntegrityError("stmt", None, _FakeOrig("23505")))
    assert not svc._is_retryable(RuntimeError("x"))


async def test_txn_b_retries_on_fresh_session_then_succeeds(
    db_session, seeded_admin, storage, site_log_session_factory, monkeypatch
):
    att = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    _, ev, n, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    row = await _att_row(db_session, eid, cid)
    stored = await storage.put(str(ev.evidence_id), _chunks(b"bytes"), attempt_no=n)

    sessions, sleeps, events = [], [], []
    real = site_log_session_factory

    class _Flaky:
        """First session raises a retryable error on its first execute;
        records the lifecycle so the fresh-session contract is provable."""

        def __init__(self, inner, fail, idx):
            self._inner, self._fail, self.idx = inner, fail, idx

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def execute(self, *a, **k):
            if self._fail:
                self._fail = False
                # Poison the identity map so reuse would be detectable.
                events.append(("execute-fail", self.idx))
                raise _dbapi("40001")
            events.append(("execute", self.idx))
            return await self._inner.execute(*a, **k)

        async def rollback(self):
            events.append(("rollback", self.idx))
            await self._inner.rollback()

        async def close(self):
            events.append(("close", self.idx))
            await self._inner.close()

    def factory():
        s = _Flaky(real(), fail=len(sessions) == 0, idx=len(sessions))
        events.append(("create", s.idx, len(s._inner.identity_map)))
        sessions.append(s)
        return s

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(svc.asyncio, "sleep", fake_sleep)
    out = await svc.complete_attachment(
        factory, actor_id=seeded_admin.user_id, event_id=eid,
        attachment_id=row.attachment_id, attempt_no=n, stored=stored,
        backend_name=storage.backend_name,
    )
    assert out.state is AttachmentState.stored
    # Distinct AsyncSession instances, each created with an EMPTY identity
    # map; the failed session is rolled back and closed BEFORE the retry
    # session exists — no failed transaction or ORM state is reused.
    assert len(sessions) == 2
    assert sessions[0]._inner is not sessions[1]._inner
    assert isinstance(sessions[0]._inner, AsyncSession)
    assert isinstance(sessions[1]._inner, AsyncSession)
    assert events[:5] == [
        ("create", 0, 0), ("execute-fail", 0), ("rollback", 0), ("close", 0), ("create", 1, 0),
    ]
    assert all(e[1] == 1 for e in events[5:] if e[0] == "execute")
    assert sleeps == [0.1]


async def test_txn_b_exhausts_retries_then_raises(
    db_session, seeded_admin, storage, site_log_session_factory, monkeypatch
):
    att = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    _, ev, n, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    row = await _att_row(db_session, eid, cid)
    stored = await storage.put(str(ev.evidence_id), _chunks(b"bytes"), attempt_no=n)
    created, sleeps = [], []

    class _AlwaysDeadlock:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def execute(self, *a, **k):
            raise _dbapi("40P01")

    def factory():
        s = _AlwaysDeadlock(site_log_session_factory())
        created.append(s._inner)
        return s

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(svc.asyncio, "sleep", fake_sleep)
    with pytest.raises(DBAPIError):
        await svc.complete_attachment(
            factory, actor_id=seeded_admin.user_id, event_id=eid,
            attachment_id=row.attachment_id, attempt_no=n, stored=stored,
            backend_name=storage.backend_name,
        )
    assert len(created) == svc.TXN_B_ATTEMPTS == 3
    assert len({id(s) for s in created}) == 3
    assert sleeps == list(svc.TXN_B_BACKOFF_SECONDS) == [0.1, 0.3]
    # Nothing completed: row still pending at the acquired attempt.
    row = await _att_row(db_session, eid, cid)
    assert (row.state, row.upload_attempt_no) == (AttachmentState.pending, n)


async def test_txn_b_does_not_retry_non_whitelisted(
    db_session, seeded_admin, storage, site_log_session_factory, monkeypatch
):
    att = _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[att]
    )
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    _, ev, n, _ = await svc.acquire_attachment(
        db_session, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a",
    )
    row = await _att_row(db_session, eid, cid)
    stored = await storage.put(str(ev.evidence_id), _chunks(b"bytes"), attempt_no=n)
    calls, sleeps = [], []

    class _Broken:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def execute(self, *a, **k):
            calls.append(1)
            raise _dbapi("23505")

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(svc.asyncio, "sleep", fake_sleep)
    with pytest.raises(DBAPIError):
        await svc.complete_attachment(
            lambda: _Broken(site_log_session_factory()),
            actor_id=seeded_admin.user_id, event_id=eid,
            attachment_id=row.attachment_id, attempt_no=n, stored=stored,
            backend_name=storage.backend_name,
        )
    assert calls == [1] and sleeps == []


# ------------------------------------------------------------- finalize


async def _upload(db, storage, factory, user, eid, cid, payload=b"bytes", mime="audio/m4a"):
    return await svc.upload_attachment(
        db, storage, factory, user=user, event_id=eid, attachment_client_id=cid,
        mime_type=mime, chunks=_chunks(payload), max_bytes=MAX_BYTES,
    )


async def test_finalize_matrix_and_idempotent_replay(
    db_session, seeded_admin, storage, site_log_session_factory
):
    a1, a2 = _att(), _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_admin, attachments=[a1, a2]
    )
    eid = res.view.event.site_log_event_id
    with pytest.raises(svc.SiteLogNotReady) as ni:
        await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    assert set(ni.value.states.values()) == {"awaiting_upload"}

    await _upload(db_session, storage, site_log_session_factory, seeded_admin, eid,
                  a1["attachment_client_id"])
    with pytest.raises(EvidenceStorageError):
        await svc.upload_attachment(
            db_session, storage, site_log_session_factory, user=seeded_admin,
            event_id=eid, attachment_client_id=a2["attachment_client_id"],
            mime_type="audio/m4a", chunks=_exploding(), max_bytes=MAX_BYTES,
        )
    view = await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    assert view.event.capture_status is CaptureStatus.partial_failed

    await _upload(db_session, storage, site_log_session_factory, seeded_admin, eid,
                  a2["attachment_client_id"], payload=b"retry ok")
    view = await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    assert view.event.capture_status is CaptureStatus.complete
    audits_before = await _count(db_session, SiteLogEventAuditLog, site_log_event_id=eid)
    view = await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    assert view.event.capture_status is CaptureStatus.complete
    assert await _count(db_session, SiteLogEventAuditLog, site_log_event_id=eid) == audits_before


# ------------------------------------------------------ job attribution


async def test_assign_relink_sync_and_completed_job_rules(
    db_session, seeded_admin, seeded_contributor, storage, site_log_session_factory
):
    job_a = await _mk_job(db_session, seeded_admin, name="A")
    job_b = await _mk_job(db_session, seeded_admin, name="B")
    done = await _mk_job(db_session, seeded_admin, name="Done", status=JobStatus.completed)

    with pytest.raises(svc.SiteLogJobCompleted):
        await _declare(db_session, storage, site_log_session_factory, seeded_contributor,
                       job_id=done.job_id, attachments=[_att()])

    att = _att()
    res = await _declare(db_session, storage, site_log_session_factory, seeded_contributor,
                         attachments=[att])
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    up = await _upload(db_session, storage, site_log_session_factory, seeded_contributor,
                       eid, cid)
    ev_id = up.evidence.evidence_id
    assert up.evidence.job_id is None

    with pytest.raises(svc.SiteLogJobCompleted):
        await svc.assign_job(db_session, user=seeded_contributor, event_id=eid, job_id=done.job_id)
    view = await svc.assign_job(
        db_session, user=seeded_contributor, event_id=eid, job_id=job_a.job_id
    )
    assert view.event.job_id == job_a.job_id
    assert (await _ev_cols(db_session, ev_id))[2] == job_a.job_id
    with pytest.raises(svc.SiteLogAlreadyAssigned):
        await svc.assign_job(db_session, user=seeded_contributor, event_id=eid, job_id=job_b.job_id)

    with pytest.raises(svc.SiteLogForbidden):
        await svc.relink_job(db_session, user=seeded_contributor, event_id=eid,
                             job_id=job_b.job_id, reason="wrong job")
    with pytest.raises(svc.SiteLogReasonRequired):
        await svc.relink_job(db_session, user=seeded_admin, event_id=eid,
                             job_id=job_b.job_id, reason="")
    with pytest.raises(svc.SiteLogSameJob):
        await svc.relink_job(db_session, user=seeded_admin, event_id=eid,
                             job_id=job_a.job_id, reason="same")
    with pytest.raises(svc.SiteLogJobCompleted):
        await svc.relink_job(db_session, user=seeded_admin, event_id=eid,
                             job_id=done.job_id, reason="into completed")
    view = await svc.relink_job(db_session, user=seeded_admin, event_id=eid,
                                job_id=job_b.job_id, reason="wrong job")
    assert view.event.job_id == job_b.job_id
    assert (await _ev_cols(db_session, ev_id))[2] == job_b.job_id
    ev_audit = (
        await db_session.execute(
            select(EvidenceAuditLog.action).where(EvidenceAuditLog.evidence_id == ev_id)
        )
    ).scalars().all()
    assert "job_linked" in ev_audit and "job_relinked" in ev_audit

    # Legacy path may not move bound Evidence independently.
    with pytest.raises(evidence_service.EvidenceBoundToEvent):
        await evidence_service.link_job(
            db_session, seeded_admin, ev_id, job_a.job_id, reason="x"
        )

    # Capture already started may finish after its Job completes; relink
    # away from a completed Job stays admin-allowed.
    job_b.status = JobStatus.completed
    await db_session.flush()
    a2 = _att()
    res2 = await _declare(db_session, storage, site_log_session_factory, seeded_admin,
                          job_id=job_a.job_id, attachments=[a2])
    job_a.status = JobStatus.completed
    await db_session.flush()
    eid2, cid2 = res2.view.event.site_log_event_id, a2["attachment_client_id"]
    await _upload(db_session, storage, site_log_session_factory, seeded_admin, eid2, cid2)
    v = await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid2)
    assert v.event.capture_status is CaptureStatus.complete
    job_c = await _mk_job(db_session, seeded_admin, name="C")
    v = await svc.relink_job(db_session, user=seeded_admin, event_id=eid2,
                             job_id=job_c.job_id, reason="misfiled")
    assert v.event.job_id == job_c.job_id


# -------------------------------------------------- security / hygiene


async def test_cross_tenant_event_is_404_with_no_audit(
    db_session, seeded_admin, storage, site_log_session_factory
):
    res = await _declare(db_session, storage, site_log_session_factory, seeded_admin,
                         body_text="x")
    eid = res.view.event.site_log_event_id
    other = uuid.UUID("00000000-0000-0000-0000-00000000dead")
    await db_session.execute(
        update(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == eid)
        .values(tenant_id=other)
    )
    before = await _count(db_session, SiteLogEventAuditLog)
    with pytest.raises(svc.SiteLogNotFound):
        await svc.get_event(db_session, seeded_admin, eid)
    with pytest.raises(svc.SiteLogNotFound):
        await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    with pytest.raises(svc.SiteLogNotFound):
        await svc.acquire_attachment(
            db_session, user=seeded_admin, event_id=eid,
            attachment_client_id=uuid.uuid4(), mime_type="text/plain",
        )
    assert await _count(db_session, SiteLogEventAuditLog) == before


async def test_audits_and_logs_are_content_free(
    db_session, seeded_admin, storage, site_log_session_factory, caplog
):
    marker = "SECRET-SITE-LOG-BODY-MARKER"
    att = _att()
    with caplog.at_level("DEBUG"):
        res = await _declare(db_session, storage, site_log_session_factory, seeded_admin,
                             body_text=marker, attachments=[att])
        eid = res.view.event.site_log_event_id
        await _upload(db_session, storage, site_log_session_factory, seeded_admin, eid,
                      att["attachment_client_id"], payload=marker.encode())
        await svc.finalize_capture(db_session, user=seeded_admin, event_id=eid)
    assert marker not in caplog.text
    for row in (await db_session.execute(select(SiteLogEventAuditLog))).scalars():
        assert marker not in str(row.changed_fields)
    for row in (await db_session.execute(select(EvidenceAuditLog))).scalars():
        assert marker not in str(row.detail)
    rev = (await db_session.execute(select(SiteLogEventRevision))).scalars().one()
    assert rev.body_text == marker  # content lives in the revision, only there


# --------------------------------------------------------- thinness pins


def test_thinness_pins_write_sites():
    src = (SERVICES_DIR / "site_log.py").read_text(encoding="utf-8")
    ev_src = (SERVICES_DIR / "evidence.py").read_text(encoding="utf-8")
    # NULL→value binding of evidence_id: exactly one site, in site_log.
    assert src.count("att.evidence_id = evidence.evidence_id") == 1
    assert src.count(".evidence_id = ") == 1
    # evidence.py never touches manifest rows at all.
    assert "att.evidence_id" not in ev_src
    assert "SiteLogEventAttachment" not in ev_src
    # Evidence row creation: legacy create_evidence + site_log Txn A only.
    assert ev_src.count("evidence = Evidence(") == 1
    assert src.count("evidence = Evidence(") == 1
    # Status writes to stored: one per module.
    assert src.count("status = EvidenceStatus.stored") == 1
    assert ev_src.count("status = EvidenceStatus.stored") == 1


def test_lock_order_invariant():
    """Every function locking manifest/Evidence rows locks the event first."""
    tree = ast.parse((SERVICES_DIR / "site_log.py").read_text(encoding="utf-8"))
    later = {"_lock_attachments", "_lock_attachment", "_lock_evidence_rows"}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in later | {"_lock_event"}
        ]
        # ast.walk is breadth-first; order by source position.
        order = [n.func.id for n in sorted(calls, key=lambda n: (n.lineno, n.col_offset))]
        if any(name in later for name in order):
            # _sync_job receives an already-locked event from its callers.
            if fn.name == "_sync_job":
                continue
            assert order and order[0] == "_lock_event", (fn.name, order)
            ev_idx = [i for i, n in enumerate(order) if n == "_lock_evidence_rows"]
            att_names = ("_lock_attachments", "_lock_attachment")
            att_idx = [i for i, n in enumerate(order) if n in att_names]
            if ev_idx and att_idx:
                assert max(att_idx) < min(ev_idx), (fn.name, order)


# ------------------------------------------ transaction ownership (ruling B)
#
# A negative or no-write Site Log result must neither commit nor discard
# the caller's unrelated state. The spy records every commit()/rollback()
# on the caller's session; the sentinel is an unrelated pending change in
# the caller's outer transaction. The scratch-DB tests in
# test_site_log_concurrency.py repeat this with REAL commits and a second
# physical connection as the observer; here the rollback harness proves
# the session-level contract and MissingGreenlet-freedom.


class _SpySession:
    """Proxy around the caller's AsyncSession counting transaction calls."""

    def __init__(self, inner):
        self._inner = inner
        self.commits = 0
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def commit(self):
        self.commits += 1
        await self._inner.commit()

    async def rollback(self):
        self.rollbacks += 1
        await self._inner.rollback()


async def _sentinel(db, admin):
    """Unrelated pending change: a flushed new Job + an unflushed rename."""
    job = await _mk_job(db, admin, name="SENTINEL-new")
    admin.full_name = "SENTINEL-renamed"  # dirty, not yet flushed
    return job


async def _sentinel_intact(db, admin, job):
    assert db.in_transaction(), "caller outer transaction was terminated"
    assert not inspect(admin).expired and not inspect(job).expired
    assert admin.full_name == "SENTINEL-renamed"  # no lazy IO, not reverted
    names = (
        await db.execute(select(Job.job_name).where(Job.job_id == job.job_id))
    ).scalars().all()
    assert names == ["SENTINEL-new"]  # still present in the outer transaction


async def test_negative_paths_never_commit_or_rollback_caller_session(
    db_session, seeded_admin, seeded_contributor, storage, site_log_session_factory
):
    # Fixture: one assigned event with a stored attachment and a pending
    # attachment, plus one unassigned, finalized event by the contributor.
    job = await _mk_job(db_session, seeded_admin, name="Assigned")
    stored_att, pending_att = _att(), _att()
    res = await _declare(
        db_session, storage, site_log_session_factory, seeded_contributor,
        job_id=job.job_id, attachments=[stored_att, pending_att],
    )
    eid = res.view.event.site_log_event_id
    await _upload(db_session, storage, site_log_session_factory, seeded_contributor,
                  eid, stored_att["attachment_client_id"])
    await svc.acquire_attachment(
        db_session, user=seeded_contributor, event_id=eid,
        attachment_client_id=pending_att["attachment_client_id"], mime_type="audio/m4a",
    )
    other = await _declare(db_session, storage, site_log_session_factory,
                           seeded_contributor, body_text="unassigned")
    other_eid = other.view.event.site_log_event_id

    sentinel_job = await _sentinel(db_session, seeded_admin)
    spy = _SpySession(db_session)
    audit_before = await _count(db_session, SiteLogEventAuditLog)
    ev_audit_before = await _count(db_session, EvidenceAuditLog)
    now = datetime.now(UTC)
    sc, ad = seeded_contributor, seeded_admin
    missing = uuid.uuid4()
    pend_cid = pending_att["attachment_client_id"]
    stored_cid = stored_att["attachment_client_id"]

    async def expect(exc, coro):
        with pytest.raises(exc):
            await coro

    async def declare_replay():
        return await svc.declare_capture(
            spy, storage, site_log_session_factory, user=sc,
            capture_client_id=other.view.event.capture_client_id, job_id=None,
            occurred_at=None, internal_location=None, body_text="unassigned",
            attachments=[], max_bytes=MAX_BYTES,
        )

    async def upload_replay():
        return await svc.upload_attachment(
            spy, storage, site_log_session_factory, user=sc, event_id=eid,
            attachment_client_id=stored_cid, mime_type="audio/m4a",
            chunks=_chunks(b"ignored"), max_bytes=MAX_BYTES,
        )

    paths = {
        # unreadable / not found
        "finalize unknown": lambda: expect(
            svc.SiteLogNotFound, svc.finalize_capture(spy, user=ad, event_id=missing)),
        "acquire unknown": lambda: expect(
            svc.SiteLogNotFound,
            svc.acquire_attachment(spy, user=ad, event_id=missing,
                                   attachment_client_id=missing, mime_type="audio/m4a")),
        "assign unknown": lambda: expect(
            svc.SiteLogNotFound,
            svc.assign_job(spy, user=ad, event_id=missing, job_id=job.job_id)),
        "relink unknown": lambda: expect(
            svc.SiteLogNotFound,
            svc.relink_job(spy, user=ad, event_id=missing, job_id=job.job_id, reason="r")),
        "reset unknown": lambda: expect(
            svc.SiteLogNotFound,
            svc.reset_attachment(spy, admin=ad, event_id=missing,
                                 attachment_client_id=missing, reason="r", now=now)),
        "fail superseded": lambda: svc._fail_attachment(
            spy, actor=ad, event_id=eid, attachment_id=missing, attempt_no=1, reason="x"),
        # readable but forbidden
        "relink forbidden": lambda: expect(
            svc.SiteLogForbidden,
            svc.relink_job(spy, user=sc, event_id=eid, job_id=job.job_id, reason="r")),
        "reset forbidden": lambda: expect(
            svc.SiteLogForbidden,
            svc.reset_attachment(spy, admin=sc, event_id=eid, attachment_client_id=pend_cid,
                                 reason="r", now=now)),
        # state conflict
        "acquire in progress": lambda: expect(
            svc.SiteLogUploadInProgress,
            svc.acquire_attachment(spy, user=sc, event_id=eid, attachment_client_id=pend_cid,
                                   mime_type="audio/m4a")),
        "assign already": lambda: expect(
            svc.SiteLogAlreadyAssigned,
            svc.assign_job(spy, user=sc, event_id=eid, job_id=job.job_id)),
        "finalize not ready": lambda: expect(
            svc.SiteLogNotReady, svc.finalize_capture(spy, user=sc, event_id=eid)),
        "reset nothing": lambda: expect(
            svc.SiteLogNothingToReset,
            svc.reset_attachment(spy, admin=ad, event_id=eid, attachment_client_id=stored_cid,
                                 reason="r", now=now)),
        "relink same job": lambda: expect(
            svc.SiteLogSameJob,
            svc.relink_job(spy, user=ad, event_id=eid, job_id=job.job_id, reason="r")),
        # idempotent no-write replay
        "upload replay": upload_replay,
        "finalize replay": lambda: svc.finalize_capture(spy, user=sc, event_id=other_eid),
        "declare identical replay": declare_replay,
    }
    for name, run in paths.items():
        await run()
        assert (spy.commits, spy.rollbacks) == (0, 0), name
        await _sentinel_intact(db_session, seeded_admin, sentinel_job)
        assert await _count(db_session, SiteLogEventAuditLog) == audit_before, name
        assert await _count(db_session, EvidenceAuditLog) == ev_audit_before, name

    # Replay results are readable domain objects: nothing expired, no lazy IO.
    up = await upload_replay()
    assert up.replay and not inspect(up.attachment).expired_attributes
    assert up.attachment.state is AttachmentState.stored and up.evidence.sha256
    view = await svc.finalize_capture(spy, user=sc, event_id=other_eid)
    assert view.event.capture_status is CaptureStatus.complete
    assert not inspect(view.event).expired_attributes
    rep = await declare_replay()
    assert not rep.created and rep.view.event.site_log_event_id == other_eid
    assert (spy.commits, spy.rollbacks) == (0, 0)


async def test_positive_write_paths_commit_explicitly(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """Documented convention (evidence.py precedent): a positive write path
    commits the caller's session once per short transaction."""
    spy = _SpySession(db_session)
    att = _att()
    res = await _declare(spy, storage, site_log_session_factory, seeded_admin,
                         attachments=[att])
    assert spy.commits == 1  # declare
    eid, cid = res.view.event.site_log_event_id, att["attachment_client_id"]
    await svc.upload_attachment(
        spy, storage, site_log_session_factory, user=seeded_admin, event_id=eid,
        attachment_client_id=cid, mime_type="audio/m4a", chunks=_chunks(b"b"),
        max_bytes=MAX_BYTES,
    )
    assert spy.commits == 2  # Txn A on the caller's session; Txn B on its own
    await svc.finalize_capture(spy, user=seeded_admin, event_id=eid)
    assert spy.commits == 3
    assert spy.rollbacks == 0


async def test_pending_inline_replay_returns_without_mutation(
    db_session, seeded_admin, storage, site_log_session_factory
):
    """A prior process died mid-upload of the inline row (pending@1). The
    declare replay must return the durable state untouched: no new attempt,
    no audit, no finalize, no transaction side effect."""
    cid = uuid.uuid4()
    res = await _declare(db_session, storage, site_log_session_factory, seeded_admin,
                         capture_client_id=cid, body_text="note")
    eid = res.view.event.site_log_event_id
    inline_id = svc.inline_attachment_id(cid)
    att_id = (await _att_row(db_session, eid, inline_id)).attachment_id
    await db_session.execute(
        update(SiteLogEventAttachment)
        .where(SiteLogEventAttachment.attachment_id == att_id)
        .values(state="pending", upload_attempt_no=1)
    )
    await db_session.execute(
        update(SiteLogEvent)
        .where(SiteLogEvent.site_log_event_id == eid)
        .values(capture_status="pending_upload")
    )
    audits = await _count(db_session, SiteLogEventAuditLog, site_log_event_id=eid)
    spy = _SpySession(db_session)
    again = await _declare(spy, storage, site_log_session_factory, seeded_admin,
                           capture_client_id=cid, body_text="note")
    assert not again.created and not again.inline_failed
    assert again.view.event.capture_status is CaptureStatus.pending_upload
    row = await _att_row(db_session, eid, inline_id)
    assert (row.state, row.upload_attempt_no) == (AttachmentState.pending, 1)
    assert await _count(db_session, SiteLogEventAuditLog, site_log_event_id=eid) == audits
    assert (spy.commits, spy.rollbacks) == (0, 0)


async def test_failed_inline_replay_502_again_then_200(
    db_session, seeded_admin, storage, tmp_path, site_log_session_factory
):
    """failed@N inline row: a replay retries; 502-equivalent (inline_failed)
    while the backend stays broken, success once it recovers."""

    class _Broken:
        backend_name = "broken"

        async def put(self, evidence_id, chunks, *, attempt_no=None):
            raise EvidenceStorageError("backend down")

        def open(self, key):
            raise AssertionError

        async def exists(self, key):
            return False

    cid = uuid.uuid4()
    first = await _declare(db_session, _Broken(), site_log_session_factory, seeded_admin,
                           capture_client_id=cid, body_text="note")
    assert first.created and first.inline_failed
    again = await _declare(db_session, _Broken(), site_log_session_factory, seeded_admin,
                           capture_client_id=cid, body_text="note")
    assert not again.created and again.inline_failed
    row = await _att_row(db_session, first.view.event.site_log_event_id,
                         svc.inline_attachment_id(cid))
    assert (row.state, row.upload_attempt_no) == (AttachmentState.failed, 2)
    ok = await _declare(db_session, storage, site_log_session_factory, seeded_admin,
                        capture_client_id=cid, body_text="note")
    assert not ok.created and not ok.inline_failed
    assert ok.view.event.capture_status is CaptureStatus.complete
    row = await _att_row(db_session, first.view.event.site_log_event_id,
                         svc.inline_attachment_id(cid))
    assert (row.state, row.upload_attempt_no) == (AttachmentState.stored, 3)
