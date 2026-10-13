"""Site Log → Task: one create key at a time, on a real database.

The rollback harness shares one connection, so it cannot show one request
waiting for another. These tests build their own scratch database
(``alembic upgrade head``, like the Site Log concurrency tests), run an
original create and its resend on two independent physical connections,
and hold the original at a controlled point: its task written, not yet
committed. The assignee is deactivated meanwhile, on a third connection.

Whatever happens to the original, the key ends with at most one task and
one ``created`` audit row: the resend waits for the original, then replays
its task (original committed) or is judged as a new create (original rolled
back). Synthetic rows only; the database is dropped afterwards.
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
    SiteLogEvent,
    SiteLogEventRevision,
    Task,
    TaskAuditLog,
)
from app.models.user import LanguageCode, User, UserRole
from app.services import tasks as task_svc

pytestmark = pytest.mark.asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_HOST, PG_PORT = "localhost", 5433
PG_USER = PG_PASS = "sitetracker"
SCRATCH_DB = "sitetracker_task_concurrency_test"
SCRATCH_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{SCRATCH_DB}"
ROOF = "Engineer asks for extra roof bracing"


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


def _user(tag: str, role: UserRole) -> User:
    return User(
        user_id=uuid.uuid4(), full_name=f"{role.value}-{tag}",
        email=f"{role.value}-{tag}@example.com", password_hash=hash_password("x"),
        role=role, language_preference=LanguageCode.en, is_active=True,
    )


@pytest.fixture
async def scene(factory):
    """An admin (the creator), an active worker to assign, and a job-less
    entry the admin wrote - committed, so every connection sees them."""
    tag = uuid.uuid4().hex[:8]
    admin, worker = _user(tag, UserRole.admin), _user(tag, UserRole.contributor)
    async with factory() as s:
        s.add_all([admin, worker])
        await s.flush()
        event = SiteLogEvent(
            site_log_event_id=uuid.uuid4(), author_user_id=admin.user_id, job_id=None,
            capture_client_id=uuid.uuid4(), capture_status=CaptureStatus.complete,
        )
        s.add(event)
        await s.flush()
        s.add(SiteLogEventRevision(
            site_log_event_id=event.site_log_event_id, revision_no=1, body_text=ROOF,
            actor_user_id=admin.user_id,
        ))
        await s.commit()
    return admin, worker, event.site_log_event_id


async def _pid(conn) -> int:
    return (await conn.execute(text("select pg_backend_pid()"))).scalar_one()


@contextlib.asynccontextmanager
async def _connection(engine):
    """A dedicated physical connection whose backend pid is known; sessions
    bound to it own real transactions (the Site Log concurrency pattern)."""
    async with engine.connect() as conn:
        pid = await _pid(conn)
        await conn.commit()
        yield conn, pid


class _HeldSession(AsyncSession):
    """The original request's session: its commit stops at a gate, so the
    request is held with its task written and its transaction still open.
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
    """(tasks, created audit rows) for one create key."""
    async with factory() as s:
        task_ids = (
            await s.execute(select(Task.task_id).where(Task.create_client_id == key))
        ).scalars().all()
        if not task_ids:
            return 0, 0
        created = (
            await s.execute(
                select(func.count()).select_from(TaskAuditLog).where(
                    TaskAuditLog.task_id.in_(task_ids), TaskAuditLog.action == "created"
                )
            )
        ).scalar_one()
        return len(task_ids), created


async def _deactivate(factory, user_id: uuid.UUID) -> None:
    async with factory() as s:
        await s.execute(update(User).where(User.user_id == user_id).values(is_active=False))
        await s.commit()


@contextlib.asynccontextmanager
async def _held(task: asyncio.Task, session: _HeldSession):
    """Never strand the held original: a failed assertion would otherwise
    leave it suspended on a checked-out connection, and the scratch database
    could not be dropped at teardown."""
    try:
        yield task
    finally:
        session.release.set()
        if not task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=30)


@pytest.mark.parametrize("original_commits", [True, False])
async def test_a_resend_waits_for_the_uncommitted_original(
    engine, factory, scene, original_commits
):
    admin, worker, event_id = scene
    key = uuid.uuid4()
    args = dict(
        user=admin, event_id=event_id, create_client_id=key, title=ROOF,
        assignee_user_id=worker.user_id, due_date=None,
    )

    async with _connection(engine) as (c1, pid1), _connection(engine) as (c2, pid2):
        assert pid1 != pid2
        original_db = _HeldSession(bind=c1, expire_on_commit=False, fail=not original_commits)
        original = asyncio.create_task(task_svc.create_task(original_db, **args))
        async with _held(original, original_db):
            # The original has validated the assignee and written its task,
            # and holds its transaction open.
            await asyncio.wait_for(original_db.reached.wait(), timeout=10)

            # The assignee is deactivated, committed, on another connection.
            await _deactivate(factory, worker.user_id)

            # The resend (the same request again, after the app gave up on
            # the first answer) waits for the original instead of judging the
            # changed assignee on its own.
            async def resend():
                async with AsyncSession(bind=c2, expire_on_commit=False) as s:
                    return await task_svc.create_task(s, **args)

            second = asyncio.create_task(resend())
            await _until_waiting(engine, pid2)
            assert not second.done()

            original_db.release.set()
            if original_commits:
                first = await asyncio.wait_for(original, timeout=30)
                replay = await asyncio.wait_for(second, timeout=30)
                # The resend answers with the original's task: a replay.
                assert first.created is True and replay.created is False
                assert replay.view.task.task_id == first.view.task.task_id
                assert await _counts(factory, key) == (1, 1)
            else:
                with pytest.raises(ConnectionResetError):
                    await asyncio.wait_for(original, timeout=30)
                # Nothing was created under the key, so the resend is a new
                # create - and the assignee is no longer active: refused.
                with pytest.raises(task_svc.TaskValidationError):
                    await asyncio.wait_for(second, timeout=30)
                assert await _counts(factory, key) == (0, 0)

    if not original_commits:
        # The app drops a refused key; the corrected save (a new key) makes
        # the one and only task from this entry.
        async with factory() as s:
            fixed = await task_svc.create_task(
                s, **{**args, "create_client_id": uuid.uuid4(), "assignee_user_id": None}
            )
        assert fixed.created is True
    async with factory() as s:
        tasks_from_entry = (
            await s.execute(
                select(func.count()).select_from(Task).where(
                    Task.source_site_log_event_id == event_id
                )
            )
        ).scalar_one()
    assert tasks_from_entry == 1


async def test_two_identical_creates_at_once_make_one_task(engine, factory, scene):
    admin, worker, event_id = scene
    key = uuid.uuid4()

    async def create(conn):
        async with AsyncSession(bind=conn, expire_on_commit=False) as s:
            res = await task_svc.create_task(
                s, user=admin, event_id=event_id, create_client_id=key, title=ROOF,
                assignee_user_id=worker.user_id, due_date=None,
            )
            return res.created, res.view.task.task_id

    async with _connection(engine) as (c1, p1), _connection(engine) as (c2, p2):
        assert p1 != p2
        results = await asyncio.gather(create(c1), create(c2))
    assert sorted(created for created, _ in results) == [False, True]
    assert len({task_id for _, task_id in results}) == 1
    assert await _counts(factory, key) == (1, 1)
