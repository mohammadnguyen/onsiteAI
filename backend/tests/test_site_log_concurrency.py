"""WP A A2a — real lock-contention tests on a scratch database.

The rollback harness shares one connection, so it cannot exercise
``FOR UPDATE`` waits. These tests build ``sitetracker_concurrency_test``
via ``alembic upgrade head`` (like the migration tests), open a real
engine with independent pooled connections and race the documented
interleavings. Each scenario must converge to a coupled, explainable
end state. Synthetic rows only; the database is dropped afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models import (
    AttachmentState,
    Evidence,
    EvidenceStatus,
    Job,
    JobStatus,
    SiteLogEvent,
    SiteLogEventAttachment,
    SiteLogEventAuditLog,
)
from app.models.user import LanguageCode, User, UserRole
from app.services import site_log as svc
from app.services.evidence_storage import (
    EvidenceStorageError,
    LocalEvidenceStorage,
    StoredObject,
)

pytestmark = pytest.mark.asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_HOST, PG_PORT = "localhost", 5433
PG_USER = PG_PASS = "sitetracker"
SCRATCH_DB = "sitetracker_concurrency_test"
SCRATCH_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{SCRATCH_DB}"
MAX_BYTES = 1024 * 1024


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = SCRATCH_URL
    env.pop("ENVIRONMENT", None)
    env.setdefault("APP_ENV", "test")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=300,
    )


async def _admin_conn():
    return await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, database="sitetracker_test"
    )


@pytest.fixture(scope="module")
async def engine():
    conn = await _admin_conn()
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await conn.close()
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
    eng = create_async_engine(SCRATCH_URL, pool_size=6, max_overflow=4)
    yield eng
    await eng.dispose()
    conn = await _admin_conn()
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    finally:
        await conn.close()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def actors(factory):
    """Fresh admin + contributor + two active jobs per test (unique emails)."""
    tag = uuid.uuid4().hex[:8]
    async with factory() as s:
        admin = User(
            user_id=uuid.uuid4(), full_name="A", email=f"admin-{tag}@example.com",
            password_hash=hash_password("x"), role=UserRole.admin,
            language_preference=LanguageCode.en, is_active=True,
        )
        contrib = User(
            user_id=uuid.uuid4(), full_name="C", email=f"c-{tag}@example.com",
            password_hash=hash_password("x"), role=UserRole.contributor,
            language_preference=LanguageCode.en, is_active=True,
        )
        s.add_all([admin, contrib])
        await s.flush()
        job_a = Job(job_id=uuid.uuid4(), job_name=f"A-{tag}", status=JobStatus.active,
                    created_by=admin.user_id)
        job_b = Job(job_id=uuid.uuid4(), job_name=f"B-{tag}", status=JobStatus.active,
                    created_by=admin.user_id)
        s.add_all([job_a, job_b])
        await s.commit()
        return admin, contrib, job_a.job_id, job_b.job_id


async def _chunks(payload: bytes):
    yield payload


async def _state(factory, event_id, client_id):
    async with factory() as s:
        att = (
            await s.execute(
                select(SiteLogEventAttachment).where(
                    SiteLogEventAttachment.site_log_event_id == event_id,
                    SiteLogEventAttachment.attachment_client_id == client_id,
                )
            )
        ).scalar_one()
        ev = None if att.evidence_id is None else await s.get(Evidence, att.evidence_id)
        event = await s.get(SiteLogEvent, event_id)
        return att, ev, event


async def _prepare(factory, storage, user, *, job_id=None):
    """Declare one external attachment and acquire attempt 1 (Txn A)."""
    cid = uuid.uuid4()
    async with factory() as s:
        res = await svc.declare_capture(
            s, storage, factory, user=user, capture_client_id=uuid.uuid4(), job_id=job_id,
            occurred_at=None, internal_location=None, body_text=None,
            attachments=[{"attachment_client_id": cid, "declared_media_type": "audio",
                          "declared_size_bytes": None}],
            max_bytes=MAX_BYTES,
        )
        eid = res.view.event.site_log_event_id
        att, ev, n, _ = await svc.acquire_attachment(
            s, user=user, event_id=eid, attachment_client_id=cid, mime_type="audio/m4a"
        )
        return eid, cid, att.attachment_id, ev.evidence_id, n


@pytest.mark.parametrize("relink_first", [True, False])
async def test_upload_completion_and_relink_converge(
    engine, factory, actors, tmp_path, relink_first
):
    admin, contrib, job_a, job_b = actors
    storage = LocalEvidenceStorage(tmp_path)
    eid, cid, att_id, ev_id, n = await _prepare(factory, storage, contrib, job_id=job_a)
    stored = await storage.put(str(ev_id), _chunks(b"race bytes"), attempt_no=n)

    async def complete():
        return await svc.complete_attachment(
            factory, actor_id=contrib.user_id, event_id=eid, attachment_id=att_id,
            attempt_no=n, stored=stored, backend_name=storage.backend_name,
        )

    async def relink():
        async with factory() as s:
            return await svc.relink_job(
                s, user=admin, event_id=eid, job_id=job_b, reason="race"
            )

    # Deterministic ordering both ways, then a genuine race.
    if relink_first:
        await relink()
        await complete()
    else:
        await complete()
        await relink()
    att, ev, event = await _state(factory, eid, cid)
    assert att.state is AttachmentState.stored and ev.status is EvidenceStatus.stored
    assert ev.job_id == event.job_id == job_b

    # Race on two dedicated physical connections (distinct backend pids).
    eid2, cid2, att_id2, ev_id2, n2 = await _prepare(factory, storage, contrib, job_id=job_a)
    stored2 = await storage.put(str(ev_id2), _chunks(b"race bytes 2"), attempt_no=n2)
    async with _connection(engine) as (c1, pid1), _connection(engine) as (c2, pid2):
        assert pid1 != pid2

        async def complete2():
            return await svc.complete_attachment(
                lambda: _bound(c1), actor_id=contrib.user_id, event_id=eid2,
                attachment_id=att_id2, attempt_no=n2, stored=stored2,
                backend_name=storage.backend_name,
            )

        async def relink2():
            async with _bound(c2) as s:
                return await svc.relink_job(s, user=admin, event_id=eid2, job_id=job_b,
                                            reason="r")

        await asyncio.gather(complete2(), relink2())
    att, ev, event = await _state(factory, eid2, cid2)
    assert att.state is AttachmentState.stored and ev.status is EvidenceStatus.stored
    assert ev.job_id == event.job_id == job_b


async def test_admin_reset_racing_old_completion_stays_coupled(
    engine, factory, actors, tmp_path
):
    admin, contrib, _, _ = actors
    storage = LocalEvidenceStorage(tmp_path)
    eid, cid, att_id, ev_id, n = await _prepare(factory, storage, contrib)
    async with factory() as s:
        await s.execute(
            update(SiteLogEventAttachment)
            .where(SiteLogEventAttachment.attachment_id == att_id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=20))
        )
        await s.commit()
    stored = await storage.put(str(ev_id), _chunks(b"late bytes"), attempt_no=n)

    async with _connection(engine) as (c1, pid1), _connection(engine) as (c2, pid2):
        assert pid1 != pid2

        async def complete():
            try:
                await svc.complete_attachment(
                    lambda: _bound(c1), actor_id=contrib.user_id, event_id=eid,
                    attachment_id=att_id, attempt_no=n, stored=stored,
                    backend_name=storage.backend_name,
                )
                return "completed"
            except svc.SiteLogAttemptSuperseded:
                return "superseded"

        async def reset():
            async with _bound(c2) as s:
                try:
                    await svc.reset_attachment(
                        s, admin=admin, event_id=eid, attachment_client_id=cid,
                        reason="stuck", now=datetime.now(UTC),
                    )
                    return "reset"
                except svc.SiteLogNothingToReset:
                    return "nothing"

        outcomes = set(await asyncio.gather(complete(), reset()))
    att, ev, _ = await _state(factory, eid, cid)
    assert outcomes in ({"completed", "nothing"}, {"superseded", "reset"}), outcomes
    if "reset" in outcomes:
        assert att.state is AttachmentState.failed and ev.status is EvidenceStatus.failed
    else:
        assert att.state is AttachmentState.stored and ev.status is EvidenceStatus.stored
    assert att.upload_attempt_no == n


async def test_obsolete_attempt_never_completes_after_newer(engine, factory, actors, tmp_path):
    admin, contrib, _, _ = actors
    storage = LocalEvidenceStorage(tmp_path)
    eid, cid, att_id, ev_id, n1 = await _prepare(factory, storage, contrib)
    async with factory() as s:
        await svc._fail_attachment(
            s, actor=contrib, event_id=eid, attachment_id=att_id, attempt_no=n1,
            reason="storage_error",
        )
    async with factory() as s:
        _, ev2, n2, _ = await svc.acquire_attachment(
            s, user=contrib, event_id=eid, attachment_client_id=cid, mime_type="audio/m4a"
        )
    assert (n1, n2) == (1, 2) and ev2.evidence_id == ev_id
    old = await storage.put(str(ev_id), _chunks(b"attempt one"), attempt_no=n1)
    new = await storage.put(str(ev_id), _chunks(b"attempt two"), attempt_no=n2)

    async with _connection(engine) as (c1, pid1), _connection(engine) as (c2, pid2):
        assert pid1 != pid2

        async def complete(conn, no, stored):
            try:
                await svc.complete_attachment(
                    lambda: _bound(conn), actor_id=contrib.user_id, event_id=eid,
                    attachment_id=att_id, attempt_no=no, stored=stored,
                    backend_name=storage.backend_name,
                )
                return no
            except svc.SiteLogAttemptSuperseded:
                return -no

        outcomes = await asyncio.gather(complete(c1, n1, old), complete(c2, n2, new))
    assert sorted(outcomes) == [-1, 2]
    att, ev, _ = await _state(factory, eid, cid)
    assert att.state is AttachmentState.stored and att.upload_attempt_no == 2
    assert ev.sha256 == new.sha256 and ev.storage_key == new.key


async def test_concurrent_identical_declares_collapse(engine, factory, actors, tmp_path):
    _, contrib, _, _ = actors
    storage = LocalEvidenceStorage(tmp_path)
    cid = uuid.uuid4()
    att = {"attachment_client_id": uuid.uuid4(), "declared_media_type": "image",
           "declared_size_bytes": 3}

    async def declare(conn):
        async with _bound(conn) as s:
            res = await svc.declare_capture(
                s, storage, lambda: _bound(conn), user=contrib, capture_client_id=cid,
                job_id=None, occurred_at=None, internal_location="Kitchen", body_text=None,
                attachments=[att], max_bytes=MAX_BYTES,
            )
            return res.created, res.view.event.site_log_event_id

    async with (
        _connection(engine) as (c1, p1),
        _connection(engine) as (c2, p2),
        _connection(engine) as (c3, p3),
    ):
        assert len({p1, p2, p3}) == 3
        results = await asyncio.gather(declare(c1), declare(c2), declare(c3))
    assert sorted(c for c, _ in results) == [False, False, True]
    assert len({e for _, e in results}) == 1
    async with factory() as s:
        n = (await s.execute(select(SiteLogEvent).where(SiteLogEvent.capture_client_id == cid)))
        assert len(n.scalars().all()) == 1


# ------------------------------------ transaction boundary, real commits
#
# Ruling B, proven with REAL transactions: the caller session holds an
# unrelated uncommitted sentinel; a negative / no-write Site Log call must
# neither commit it (a second physical connection would see it) nor
# discard it (the caller's own transaction would no longer see it).


class _SpySession(AsyncSession):
    """Caller session that counts outer commit()/rollback() calls."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        await super().commit()

    async def rollback(self):
        self.rollbacks += 1
        await super().rollback()


async def _pid(conn_or_session) -> int:
    return (await conn_or_session.execute(text("select pg_backend_pid()"))).scalar_one()


@contextlib.asynccontextmanager
async def _connection(engine):
    """A dedicated physical connection whose backend pid is known; the
    initial pid read is committed so sessions bound to it own their own
    transactions (and their commits are real)."""
    async with engine.connect() as conn:
        pid = await _pid(conn)
        await conn.commit()
        yield conn, pid


def _bound(conn) -> AsyncSession:
    return AsyncSession(bind=conn, expire_on_commit=False)


async def _observed(engine, sentinel_name) -> tuple[int, int]:
    """(rows visible, observer pid) from an independent connection."""
    async with engine.connect() as conn:
        n = (
            await conn.execute(
                select(func.count()).select_from(Job).where(Job.job_name == sentinel_name)
            )
        ).scalar_one()
        return n, await _pid(conn)


async def test_negative_paths_do_not_commit_or_discard_caller_state(
    engine, factory, actors, tmp_path
):
    admin, contrib, job_a, job_b = actors
    storage = LocalEvidenceStorage(tmp_path)
    # Assigned event by the contributor: one stored + one pending attachment.
    stored_cid, pending_cid = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        res = await svc.declare_capture(
            s, storage, factory, user=contrib, capture_client_id=uuid.uuid4(), job_id=job_a,
            occurred_at=None, internal_location=None, body_text=None,
            attachments=[
                {"attachment_client_id": stored_cid, "declared_media_type": "audio",
                 "declared_size_bytes": None},
                {"attachment_client_id": pending_cid, "declared_media_type": "audio",
                 "declared_size_bytes": None},
            ],
            max_bytes=MAX_BYTES,
        )
        eid = res.view.event.site_log_event_id
        await svc.upload_attachment(
            s, storage, factory, user=contrib, event_id=eid, attachment_client_id=stored_cid,
            mime_type="audio/m4a", chunks=_chunks(b"stored"), max_bytes=MAX_BYTES,
        )
        await svc.acquire_attachment(
            s, user=contrib, event_id=eid, attachment_client_id=pending_cid,
            mime_type="audio/m4a",
        )
    async with factory() as s:
        audit_before = (
            await s.execute(select(func.count()).select_from(SiteLogEventAuditLog))
        ).scalar_one()

    spy_factory = async_sessionmaker(engine, class_=_SpySession, expire_on_commit=False)
    now = datetime.now(UTC)
    missing = uuid.uuid4()

    async def expect(exc, coro):
        with pytest.raises(exc):
            await coro

    def paths(db):
        return {
            "not found": lambda: expect(
                svc.SiteLogNotFound, svc.finalize_capture(db, user=admin, event_id=missing)),
            "forbidden": lambda: expect(
                svc.SiteLogForbidden,
                svc.relink_job(db, user=contrib, event_id=eid, job_id=job_b, reason="r")),
            "conflict": lambda: expect(
                svc.SiteLogUploadInProgress,
                svc.acquire_attachment(db, user=contrib, event_id=eid,
                                       attachment_client_id=pending_cid,
                                       mime_type="audio/m4a")),
            "conflict-reset-nothing": lambda: expect(
                svc.SiteLogNothingToReset,
                svc.reset_attachment(db, admin=admin, event_id=eid,
                                     attachment_client_id=stored_cid, reason="r", now=now)),
            "replay": lambda: svc.upload_attachment(
                db, storage, factory, user=contrib, event_id=eid,
                attachment_client_id=stored_cid, mime_type="audio/m4a",
                chunks=_chunks(b"ignored"), max_bytes=MAX_BYTES),
        }

    for name in paths(None):
        sentinel = f"SENTINEL-{uuid.uuid4().hex[:8]}"
        async with spy_factory() as db:
            caller_pid = await _pid(db)
            db.add(Job(job_id=uuid.uuid4(), job_name=sentinel, status=JobStatus.active,
                       created_by=admin.user_id))
            await db.flush()  # in the caller's outer transaction, uncommitted
            contrib.full_name = "unchanged"  # detached object: attribute read must not do IO

            await paths(db)[name]()

            assert (db.commits, db.rollbacks) == (0, 0), name
            assert db.in_transaction(), name
            # Not committed: an independent physical connection cannot see it.
            visible, observer_pid = await _observed(engine, sentinel)
            assert visible == 0 and observer_pid != caller_pid, name
            # Not discarded: the caller's own transaction still holds it.
            mine = (
                await db.execute(
                    select(func.count()).select_from(Job).where(Job.job_name == sentinel)
                )
            ).scalar_one()
            assert mine == 1, name
            # Locks released by the SAVEPOINT rollback: another connection can
            # lock the event right now (NOWAIT would raise 55P03 otherwise).
            async with engine.connect() as c2:
                locked = (
                    await c2.execute(
                        select(SiteLogEvent.site_log_event_id)
                        .where(SiteLogEvent.site_log_event_id == eid)
                        .with_for_update(nowait=True)
                    )
                ).scalar_one()
                assert locked == eid, name
                await c2.rollback()
            await db.rollback()  # caller decides; the sentinel never reached the DB
        visible, _ = await _observed(engine, sentinel)
        assert visible == 0, name
    async with factory() as s:
        audit_after = (
            await s.execute(select(func.count()).select_from(SiteLogEventAuditLog))
        ).scalar_one()
    assert audit_after == audit_before


async def test_positive_path_commits_caller_transaction(engine, factory, actors, tmp_path):
    """Disclosed convention: a positive write commits the caller's session
    (short transactions around byte streaming, evidence.py precedent)."""
    admin, contrib, job_a, _ = actors
    storage = LocalEvidenceStorage(tmp_path)
    sentinel = f"SENTINEL-{uuid.uuid4().hex[:8]}"
    spy_factory = async_sessionmaker(engine, class_=_SpySession, expire_on_commit=False)
    async with spy_factory() as db:
        db.add(Job(job_id=uuid.uuid4(), job_name=sentinel, status=JobStatus.active,
                   created_by=admin.user_id))
        await db.flush()
        await svc.declare_capture(
            db, storage, factory, user=contrib, capture_client_id=uuid.uuid4(), job_id=job_a,
            occurred_at=None, internal_location=None, body_text=None,
            attachments=[{"attachment_client_id": uuid.uuid4(), "declared_media_type": "audio",
                          "declared_size_bytes": None}],
            max_bytes=MAX_BYTES,
        )
        assert (db.commits, db.rollbacks) == (1, 0)
    visible, _ = await _observed(engine, sentinel)
    assert visible == 1


# ------------------------------------------------------------------
# A2a.2 inline-text integrity, raced on a real database.
# ------------------------------------------------------------------


async def _inline_prepare(factory, storage, user, body="Poured 12m3 bay 3"):
    """Declare inline text whose server-side upload fails, leaving the
    reserved row in the state that opens the substitution window."""
    cid = uuid.uuid4()
    async with factory() as s:
        res = await svc.declare_capture(
            s, storage, factory, user=user, capture_client_id=cid, job_id=None,
            occurred_at=None, internal_location=None, body_text=body,
            attachments=[], max_bytes=MAX_BYTES,
        )
        return res.view.event.site_log_event_id, svc.inline_attachment_id(cid), cid, body


class _FailOnce(LocalEvidenceStorage):
    def __init__(self, root):
        super().__init__(root)
        self.failed = False

    async def put(self, evidence_id, chunks, attempt_no=None):
        if not self.failed:
            self.failed = True
            async for _ in chunks:
                pass
            raise EvidenceStorageError("backend unavailable")
        return await super().put(evidence_id, chunks, attempt_no=attempt_no)


class _ServerGate:
    """A sync point inside the server's own inline path.

    ``acquire_attachment`` is wrapped so the SERVER call (``internal=True``)
    stops at a known place and waits to be released. The client's call is
    never gated, so the interleaving under test is produced deliberately
    rather than hoped for: ``asyncio.gather`` starting two coroutines is not
    evidence that they contended for anything.

    ``before`` gates the call BEFORE Txn A runs - the window after the
    declare has committed and before the server owns the row. ``after``
    gates it once the server holds the row pending at its new attempt.
    """

    def __init__(self, monkeypatch, *, when):
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.when = when
        self._real = svc.acquire_attachment

        async def wrapper(db, **kw):
            if not kw.get("internal"):
                return await self._real(db, **kw)
            if self.when == "before":
                self.reached.set()
                await self.release.wait()
                return await self._real(db, **kw)
            out = await self._real(db, **kw)
            self.reached.set()
            await self.release.wait()
            return out

        monkeypatch.setattr(svc, "acquire_attachment", wrapper)


@contextlib.asynccontextmanager
async def _gated_server(gate, coro):
    """Run the gated server task so it can never be left suspended.

    A client assertion that fails before the gate is released would
    otherwise strand the server for ever, still holding the connection it
    read revision 1 on - and this module drops its scratch database at
    teardown, which a checked-out connection blocks. A reservation
    regression, the very failure these tests exist to catch, would then
    surface as a hung teardown instead of a clean assertion.

    The caller still awaits the task explicitly on the happy path, so a
    server-side failure propagates rather than being swallowed here.
    """
    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        gate.release.set()
        if not task.done():
            task.cancel()
            # Only the cancellation we just requested is suppressed. A real
            # server error still surfaces - and if the body is already
            # unwinding, that error is the more informative one.
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _audit_count(factory, event_id):
    async with factory() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(SiteLogEventAuditLog)
                .where(SiteLogEventAuditLog.site_log_event_id == event_id)
            )
        ).scalar_one()


async def _read_back(storage, factory, event_id, client_id):
    att, ev, _ = await _state(factory, event_id, client_id)
    assert ev is not None and ev.storage_key, (att.state, ev)
    return b"".join([c async for c in storage.open(ev.storage_key)])


async def test_a_client_put_racing_the_server_retry_never_wins(
    engine, factory, actors, tmp_path, monkeypatch
):
    """A REAL race on a real database, at a controlled point.

    The server's recovery is held at the moment it owns the inline row at a
    fresh attempt; the client's PUT then arrives on its own connection and
    contends for that row. Both outcomes are asserted separately, so a
    server-side failure cannot hide behind the client's refusal.
    """
    admin, _contrib, _job_a, _job_b = actors
    storage = _FailOnce(tmp_path)
    eid, inline_id, cid, body = await _inline_prepare(factory, storage, admin)
    gate = _ServerGate(monkeypatch, when="after")

    async def server_replay():
        async with factory() as s:
            return await svc.declare_capture(
                s, storage, factory, user=admin, capture_client_id=cid,
                job_id=None, occurred_at=None, internal_location=None,
                body_text=body, attachments=[], max_bytes=MAX_BYTES,
            )

    async with _gated_server(gate, server_replay()) as server:
        await asyncio.wait_for(gate.reached.wait(), timeout=10)

        # The server now holds the row pending at its new attempt. The
        # client arrives here, on an independent connection.
        client_error = None
        async with factory() as s:
            try:
                await svc.upload_attachment(
                    s, storage, factory, user=admin, event_id=eid,
                    attachment_client_id=inline_id,
                    mime_type="text/plain; charset=utf-8",
                    chunks=_chunks(b"Poured 2m3 bay 3"), max_bytes=MAX_BYTES,
                )
            except Exception as exc:  # noqa: BLE001 - the type is the assertion
                client_error = exc

        # Refused by the reservation itself, not by an incidental
        # in-progress conflict: the guard runs before the state branches.
        assert isinstance(client_error, svc.SiteLogInlineReserved), client_error

        gate.release.set()
        result = await asyncio.wait_for(server, timeout=30)  # re-raises server failures
        assert not result.inline_failed

    att, ev, _ = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.stored
    assert ev.status is EvidenceStatus.stored
    assert await _read_back(storage, factory, eid, inline_id) == body.encode("utf-8")


async def test_a_client_put_in_the_declare_window_is_refused(
    engine, factory, actors, tmp_path, monkeypatch
):
    """The process-death window, reached through the real lifecycle.

    The server is stopped after the declare has COMMITTED and before it
    acquires the inline row, which is exactly the state a process death
    leaves behind - the row is awaiting_upload at attempt 0 because the
    service put it there, not because a test rewrote it. The client's PUT
    runs on an independent connection inside that window.
    """
    admin, _contrib, _job_a, _job_b = actors
    storage = LocalEvidenceStorage(tmp_path)
    gate = _ServerGate(monkeypatch, when="before")
    cid, body = uuid.uuid4(), "Poured 12m3 bay 3"
    inline_id = svc.inline_attachment_id(cid)

    async def declare():
        async with factory() as s:
            return await svc.declare_capture(
                s, storage, factory, user=admin, capture_client_id=cid,
                job_id=None, occurred_at=None, internal_location=None,
                body_text=body, attachments=[], max_bytes=MAX_BYTES,
            )

    async with _gated_server(gate, declare()) as server:
        await asyncio.wait_for(gate.reached.wait(), timeout=10)

        # The declare is committed and visible on another connection; the
        # row exists and the server has not taken it yet.
        async with factory() as s:
            event = (
                await s.execute(
                    select(SiteLogEvent).where(SiteLogEvent.capture_client_id == cid)
                )
            ).scalar_one()
            eid = event.site_log_event_id
        att, _ev, _ = await _state(factory, eid, inline_id)
        assert att.state is AttachmentState.awaiting_upload
        assert att.upload_attempt_no == 0

        client_error = None
        async with factory() as s:
            try:
                await svc.upload_attachment(
                    s, storage, factory, user=admin, event_id=eid,
                    attachment_client_id=inline_id,
                    mime_type="text/plain; charset=utf-8",
                    chunks=_chunks(b"Poured 2m3 bay 3"), max_bytes=MAX_BYTES,
                )
            except Exception as exc:  # noqa: BLE001 - the type is the assertion
                client_error = exc
        assert isinstance(client_error, svc.SiteLogInlineReserved), client_error

        gate.release.set()
        result = await asyncio.wait_for(server, timeout=30)
        assert not result.inline_failed

    att, ev, _ = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.stored and ev.status is EvidenceStatus.stored
    assert await _read_back(storage, factory, eid, inline_id) == body.encode("utf-8")


async def test_an_admin_reset_reopens_nothing_for_a_client(
    engine, factory, actors, tmp_path
):
    """The third entry window, produced by actually resetting.

    The row is made pending by the server's own Txn A, then an admin reset
    moves it pending -> failed through the real service call. A client PUT
    into that window is refused and writes nothing, and the legitimate
    server replay still recovers revision 1's text.
    """
    admin, _contrib, _job_a, _job_b = actors
    storage = _FailOnce(tmp_path)
    eid, inline_id, cid, body = await _inline_prepare(factory, storage, admin)

    # Server-side Txn A only, on the failed row: it is left pending at a new
    # attempt, which is the state a reset exists for. Reached through the
    # service, not by writing the row.
    async with factory() as s:
        await svc.acquire_attachment(
            s, user=admin, event_id=eid, attachment_client_id=inline_id,
            mime_type="text/plain; charset=utf-8", internal=True,
        )

    att, _ev, _ = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.pending

    async with factory() as s:
        await svc.reset_attachment(
            s, admin=admin, event_id=eid, attachment_client_id=inline_id,
            reason="stuck upload", now=att.updated_at + timedelta(minutes=16),
        )
    att, _ev, _ = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.failed

    before_state = (att.state, att.upload_attempt_no)
    before_audit = await _audit_count(factory, eid)

    client_error = None
    async with factory() as s:
        try:
            await svc.upload_attachment(
                s, storage, factory, user=admin, event_id=eid,
                attachment_client_id=inline_id,
                mime_type="text/plain; charset=utf-8",
                chunks=_chunks(b"Poured 2m3 bay 3"), max_bytes=MAX_BYTES,
            )
        except Exception as exc:  # noqa: BLE001 - the type is the assertion
            client_error = exc
    assert isinstance(client_error, svc.SiteLogInlineReserved), client_error

    att, _ev, _ = await _state(factory, eid, inline_id)
    assert (att.state, att.upload_attempt_no) == before_state  # no state written
    assert await _audit_count(factory, eid) == before_audit    # no audit written

    async with factory() as s:
        replay = await svc.declare_capture(
            s, storage, factory, user=admin, capture_client_id=cid, job_id=None,
            occurred_at=None, internal_location=None, body_text=body,
            attachments=[], max_bytes=MAX_BYTES,
        )
    assert not replay.inline_failed
    att, ev, _ = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.stored and ev.status is EvidenceStatus.stored
    assert await _read_back(storage, factory, eid, inline_id) == body.encode("utf-8")


async def test_an_obsolete_attempt_cannot_fail_the_newer_one(
    engine, factory, actors, tmp_path
):
    """An old attempt arriving late with a bad receipt must be rejected by
    the CAS, not allowed to mark the current attempt content_mismatch."""
    admin, _contrib, _job_a, _job_b = actors
    storage = _FailOnce(tmp_path)
    eid, inline_id, cid, body = await _inline_prepare(factory, storage, admin)

    async with factory() as s:
        att = (
            await s.execute(
                select(SiteLogEventAttachment).where(
                    SiteLogEventAttachment.site_log_event_id == eid,
                    SiteLogEventAttachment.attachment_client_id == inline_id,
                )
            )
        ).scalar_one()
        att_id, old_attempt = att.attachment_id, att.upload_attempt_no

    # A newer attempt supersedes it.
    async with factory() as s:
        await svc.declare_capture(
            s, storage, factory, user=admin, capture_client_id=cid, job_id=None,
            occurred_at=None, internal_location=None, body_text=body,
            attachments=[], max_bytes=MAX_BYTES,
        )

    bogus = StoredObject(key="evidence/x/deadbeef.a1", sha256="e" * 64, size_bytes=1)
    with pytest.raises(svc.SiteLogAttemptSuperseded):
        await svc.complete_attachment(
            factory, actor_id=admin.user_id, event_id=eid, attachment_id=att_id,
            attempt_no=old_attempt, stored=bogus, backend_name=storage.backend_name,
        )

    att, ev, _event = await _state(factory, eid, inline_id)
    assert att.state is AttachmentState.stored
    assert ev.status is EvidenceStatus.stored
