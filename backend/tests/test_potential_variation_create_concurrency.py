"""Potential Variations: one create key at a time, on a real database.

The rollback harness shares one connection, so it cannot show one request
waiting for another. These tests build their own scratch database
(``alembic upgrade head``, like the task concurrency tests), run an original
flag and its resend on two independent physical connections, and hold the
original at a controlled point: its flag written, not yet committed. The
entry changes meanwhile, on a third connection: corrected (a new revision),
or put under a job (the person confirmed "no job").

Whatever happens to the original, the key ends with at most one flag and one
``created`` audit row: the resend waits for the original, then replays its
flag (original committed - never ``source_changed`` or ``job_changed``) or
is judged as a new flag (original rolled back - and then the changed entry
IS a change the person has not seen). Synthetic rows only; the database is
dropped afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models import (
    CaptureStatus,
    Job,
    JobStatus,
    PotentialVariation,
    PotentialVariationAuditLog,
    SiteLogEvent,
    SiteLogEventRevision,
)
from app.models.user import LanguageCode, User, UserRole
from app.services import potential_variations as pv_svc

pytestmark = pytest.mark.asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_HOST, PG_PORT = "localhost", 5433
PG_USER = PG_PASS = "sitetracker"
SCRATCH_DB = "sitetracker_pv_concurrency_test"
SCRATCH_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{SCRATCH_DB}"
KITCHEN = "Owner asked on site for two extra power points in the kitchen"


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
    # Only used to create and drop the scratch database itself.
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
async def scene(factory):
    """A contributor (the flagger) and a job-less entry they wrote -
    committed, so every connection sees them."""
    tag = uuid.uuid4().hex[:8]
    flagger = User(
        user_id=uuid.uuid4(), full_name=f"flagger-{tag}", email=f"flagger-{tag}@example.com",
        password_hash=hash_password("x"), role=UserRole.contributor,
        language_preference=LanguageCode.en, is_active=True,
    )
    async with factory() as s:
        s.add(flagger)
        await s.flush()
        event = SiteLogEvent(
            site_log_event_id=uuid.uuid4(), author_user_id=flagger.user_id, job_id=None,
            capture_client_id=uuid.uuid4(), capture_status=CaptureStatus.complete,
        )
        s.add(event)
        await s.flush()
        s.add(SiteLogEventRevision(
            site_log_event_id=event.site_log_event_id, revision_no=1, body_text=KITCHEN,
            actor_user_id=flagger.user_id,
        ))
        await s.commit()
    return flagger, event.site_log_event_id


async def _pid(conn) -> int:
    return (await conn.execute(text("select pg_backend_pid()"))).scalar_one()


@contextlib.asynccontextmanager
async def _connection(engine):
    """A dedicated physical connection whose backend pid is known."""
    async with engine.connect() as conn:
        pid = await _pid(conn)
        await conn.commit()
        yield conn, pid


class _HeldSession(AsyncSession):
    """The original request's session: its commit stops at a gate, so the
    request is held with its flag written and its transaction still open.
    Released, it commits - or, with ``fail``, rolls back as a request that
    dies before committing would."""

    def __init__(self, *a, fail: bool = False, **k):
        super().__init__(*a, **k)
        self.fail = fail
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def commit(self):
        self.reached.set()
        await self.release.wait()
        if self.fail:
            await super().rollback()
            raise ConnectionResetError("the request died before committing")
        await super().commit()


async def _waiting_on_advisory_lock(engine, pid: int) -> bool:
    async with engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "select count(*) from pg_locks "
                    "where locktype = 'advisory' and not granted and pid = :pid"
                ),
                {"pid": pid},
            )
        ).scalar_one()
        return n > 0


async def _until_waiting(engine, pid: int) -> None:
    for _ in range(100):
        if await _waiting_on_advisory_lock(engine, pid):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the resend never waited for the original's lock")


async def _counts(factory, key: uuid.UUID) -> tuple[int, int]:
    """(flags, created audit rows) for one create key."""
    async with factory() as s:
        ids = (
            await s.execute(
                select(PotentialVariation.potential_variation_id).where(
                    PotentialVariation.create_client_id == key
                )
            )
        ).scalars().all()
        if not ids:
            return 0, 0
        created = (
            await s.execute(
                select(func.count()).select_from(PotentialVariationAuditLog).where(
                    PotentialVariationAuditLog.potential_variation_id.in_(ids),
                    PotentialVariationAuditLog.action == "created",
                )
            )
        ).scalar_one()
        return len(ids), created


async def _correct_entry(factory, event_id: uuid.UUID, author_id: uuid.UUID) -> None:
    async with factory() as s:
        s.add(SiteLogEventRevision(
            site_log_event_id=event_id, revision_no=2, body_text="Owner now wants four",
            reason="corrected", actor_user_id=author_id,
        ))
        await s.commit()


async def _put_entry_on_a_job(factory, event_id: uuid.UUID, author_id: uuid.UUID) -> None:
    """The entry the person saw with no job gets one (what assign-job does
    to the event row; no attachments or evidence to carry along here)."""
    async with factory() as s:
        job = Job(job_id=uuid.uuid4(), job_name="Simulated job", status=JobStatus.active,
                  created_by=author_id)
        s.add(job)
        await s.flush()
        await s.execute(
            update(SiteLogEvent)
            .where(SiteLogEvent.site_log_event_id == event_id)
            .values(job_id=job.job_id)
        )
        await s.commit()


# What changes on the entry while the original is held, and what a NEW flag
# of what the person saw is refused as once nothing exists under the key.
CHANGES = {
    "corrected": (_correct_entry, pv_svc.PvSourceChanged),
    "put_on_a_job": (_put_entry_on_a_job, pv_svc.PvJobChanged),
}


@contextlib.asynccontextmanager
async def _held(task: asyncio.Task, session: _HeldSession):
    """Never strand the held original (see the task concurrency tests)."""
    try:
        yield task
    finally:
        session.release.set()
        if not task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=30)


@pytest.mark.parametrize("change", sorted(CHANGES))
@pytest.mark.parametrize("original_commits", [True, False])
async def test_a_resend_waits_for_the_uncommitted_original(
    engine, factory, scene, original_commits, change
):
    flagger, event_id = scene
    change_entry, refusal = CHANGES[change]
    key = uuid.uuid4()
    args = dict(
        user=flagger, event_id=event_id, create_client_id=key, title=KITCHEN,
        source_revision_no=1, job_id=None,
    )

    async with _connection(engine) as (c1, pid1), _connection(engine) as (c2, pid2):
        assert pid1 != pid2
        original_db = _HeldSession(bind=c1, expire_on_commit=False, fail=not original_commits)
        original = asyncio.create_task(pv_svc.create_pv(original_db, **args))
        async with _held(original, original_db):
            # The original has checked the version and the job, written its
            # flag, and holds its transaction open.
            await asyncio.wait_for(original_db.reached.wait(), timeout=10)

            # The entry changes, committed, on another connection.
            await change_entry(factory, event_id, flagger.user_id)

            async def resend():
                async with AsyncSession(bind=c2, expire_on_commit=False) as s:
                    return await pv_svc.create_pv(s, **args)

            second = asyncio.create_task(resend())
            await _until_waiting(engine, pid2)
            assert not second.done()

            original_db.release.set()
            if original_commits:
                first = await asyncio.wait_for(original, timeout=30)
                replay = await asyncio.wait_for(second, timeout=30)
                # The resend answers with the original's flag: a replay,
                # not "source_changed" or "job_changed".
                assert first.created is True and replay.created is False
                assert (
                    replay.view.pv.potential_variation_id
                    == first.view.pv.potential_variation_id
                )
                assert await _counts(factory, key) == (1, 1)
            else:
                with pytest.raises(ConnectionResetError):
                    await asyncio.wait_for(original, timeout=30)
                # Nothing was made under the key, so the resend is a new
                # flag - of an entry that is no longer what the person saw:
                # refused, as a changed version or a changed job.
                with pytest.raises(refusal):
                    await asyncio.wait_for(second, timeout=30)
                assert await _counts(factory, key) == (0, 0)


async def test_two_identical_flags_at_once_make_one(engine, factory, scene):
    flagger, event_id = scene
    key = uuid.uuid4()

    async def create(conn):
        async with AsyncSession(bind=conn, expire_on_commit=False) as s:
            res = await pv_svc.create_pv(
                s, user=flagger, event_id=event_id, create_client_id=key, title=KITCHEN,
                source_revision_no=1, job_id=None,
            )
            return res.created, res.view.pv.potential_variation_id

    async with _connection(engine) as (c1, p1), _connection(engine) as (c2, p2):
        assert p1 != p2
        results = await asyncio.gather(create(c1), create(c2))
    assert sorted(created for created, _ in results) == [False, True]
    assert len({pv_id for _, pv_id in results}) == 1
    assert await _counts(factory, key) == (1, 1)
