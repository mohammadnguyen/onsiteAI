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
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models import (
    AttachmentState,
    Evidence,
    EvidenceStatus,
    Job,
    JobStatus,
    SiteLogEvent,
    SiteLogEventAttachment,
)
from app.models.user import LanguageCode, User, UserRole
from app.services import site_log as svc
from app.services.evidence_storage import LocalEvidenceStorage

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

    # Race: a second attachment on a fresh event, both tasks concurrently.
    eid2, cid2, att_id2, ev_id2, n2 = await _prepare(factory, storage, contrib, job_id=job_a)
    stored2 = await storage.put(str(ev_id2), _chunks(b"race bytes 2"), attempt_no=n2)

    async def complete2():
        return await svc.complete_attachment(
            factory, actor_id=contrib.user_id, event_id=eid2, attachment_id=att_id2,
            attempt_no=n2, stored=stored2, backend_name=storage.backend_name,
        )

    async def relink2():
        async with factory() as s:
            return await svc.relink_job(s, user=admin, event_id=eid2, job_id=job_b, reason="r")

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

    async def complete():
        try:
            await svc.complete_attachment(
                factory, actor_id=contrib.user_id, event_id=eid, attachment_id=att_id,
                attempt_no=n, stored=stored, backend_name=storage.backend_name,
            )
            return "completed"
        except svc.SiteLogAttemptSuperseded:
            return "superseded"

    async def reset():
        async with factory() as s:
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

    async def complete(no, stored):
        try:
            await svc.complete_attachment(
                factory, actor_id=contrib.user_id, event_id=eid, attachment_id=att_id,
                attempt_no=no, stored=stored, backend_name=storage.backend_name,
            )
            return no
        except svc.SiteLogAttemptSuperseded:
            return -no

    outcomes = await asyncio.gather(complete(n1, old), complete(n2, new))
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

    async def declare():
        async with factory() as s:
            res = await svc.declare_capture(
                s, storage, factory, user=contrib, capture_client_id=cid, job_id=None,
                occurred_at=None, internal_location="Kitchen", body_text=None,
                attachments=[att], max_bytes=MAX_BYTES,
            )
            return res.created, res.view.event.site_log_event_id

    results = await asyncio.gather(declare(), declare(), declare())
    assert sorted(c for c, _ in results) == [False, False, True]
    assert len({e for _, e in results}) == 1
    async with factory() as s:
        n = (await s.execute(select(SiteLogEvent).where(SiteLogEvent.capture_client_id == cid)))
        assert len(n.scalars().all()) == 1
