"""Real two-transaction concurrency tests for the audit race fixes.

These deliberately bypass the shared ``db_session`` fixture (which wraps the
whole test in one rolled-back transaction) and open independent
``AsyncSession``s on the real test engine so two requests genuinely race at
the database. Each test commits its setup rows and cleans them up in a
``finally`` so the committed state never leaks into other tests.

Covers:

* A-1 / D-3 — two concurrent last-two-admin demotions must NOT both succeed
  (would leave zero admins). The ``SELECT ... FOR UPDATE`` on the active-admin
  set serialises them so exactly one wins.
* D-2 — two concurrent FIRST labour inserts for one worker/date into two
  different jobs must NOT both succeed (would give a 2.0-day total). The
  per-(worker, date) advisory lock serialises them so exactly one wins.
"""

from __future__ import annotations

import asyncio
import datetime as _datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Job, JobStatus, LabourEntry, User, Worker
from app.models.user import LanguageCode, UserRole
from app.schemas.labour import LabourBatchItem
from app.services import labour as labour_svc
from app.services import users as users_svc


@pytest.mark.asyncio
async def test_concurrent_last_admin_demotions_do_not_zero_out(_test_engine):
    """Two concurrent demotions of the last two admins → exactly one succeeds."""
    a1_id, a2_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSession(_test_engine) as s:
        s.add_all(
            [
                User(
                    user_id=uid,
                    full_name=f"Race Admin {n}",
                    email=f"race-admin-{uid.hex}@example.com",
                    password_hash=hash_password("pw"),
                    role=UserRole.admin,
                    language_preference=LanguageCode.en,
                    is_active=True,
                )
                for n, uid in enumerate((a1_id, a2_id))
            ]
        )
        await s.commit()

    async def _demote(user_id: uuid.UUID) -> str:
        async with AsyncSession(_test_engine) as s:
            try:
                await users_svc.update_user(s, user_id, is_active=False)
                await s.commit()
                return "ok"
            except users_svc.LastAdminProtected:
                await s.rollback()
                return "protected"

    try:
        results = await asyncio.gather(_demote(a1_id), _demote(a2_id))
        # Exactly one demotion committed; the other hit the last-admin guard.
        assert sorted(results) == ["ok", "protected"], results
        async with AsyncSession(_test_engine) as s:
            remaining = (
                await s.execute(
                    select(func.count())
                    .select_from(User)
                    .where(
                        User.user_id.in_([a1_id, a2_id]),
                        User.role == UserRole.admin,
                        User.is_active.is_(True),
                    )
                )
            ).scalar_one()
        assert remaining == 1, "the last admin must never be demoted to zero"
    finally:
        async with AsyncSession(_test_engine) as s:
            await s.execute(delete(User).where(User.user_id.in_([a1_id, a2_id])))
            await s.commit()


@pytest.mark.asyncio
async def test_concurrent_first_labour_inserts_respect_daily_cap(_test_engine):
    """Two concurrent full-day inserts for one worker/date (different jobs) →
    exactly one succeeds; the worker's daily total stays at 1.0."""
    admin_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    job1_id, job2_id = uuid.uuid4(), uuid.uuid4()
    work_date = _datetime.date.today()

    async with AsyncSession(_test_engine) as s:
        s.add(
            User(
                user_id=admin_id,
                full_name="Race Recorder",
                email=f"race-rec-{admin_id.hex}@example.com",
                password_hash=hash_password("pw"),
                role=UserRole.admin,
                language_preference=LanguageCode.en,
                is_active=True,
            )
        )
        # Flush the user before the FK-dependent worker/job inserts.
        await s.flush()
        s.add(Worker(worker_id=worker_id, display_name="Race Worker", created_by=admin_id))
        s.add_all(
            [
                Job(
                    job_id=jid,
                    job_code=f"RJ-{jid.hex[:6]}",
                    job_name=f"Race Job {n}",
                    status=JobStatus.active,
                    created_by=admin_id,
                )
                for n, jid in enumerate((job1_id, job2_id))
            ]
        )
        await s.commit()

    async def _tick(job_id: uuid.UUID) -> str:
        async with AsyncSession(_test_engine) as s:
            admin = await s.get(User, admin_id)
            try:
                await labour_svc.batch_upsert_entries(
                    s,
                    current_user=admin,
                    job_id=job_id,
                    work_date=work_date,
                    items=[LabourBatchItem(worker_id=worker_id, day_fraction=Decimal("1.0"))],
                )
                await s.commit()
                return "ok"
            except labour_svc.LabourValidationError:
                await s.rollback()
                return "rejected"

    try:
        results = await asyncio.gather(_tick(job1_id), _tick(job2_id))
        assert sorted(results) == ["ok", "rejected"], results
        async with AsyncSession(_test_engine) as s:
            total = (
                await s.execute(
                    select(func.coalesce(func.sum(LabourEntry.day_fraction), 0)).where(
                        LabourEntry.worker_id == worker_id,
                        LabourEntry.work_date == work_date,
                    )
                )
            ).scalar_one()
        assert Decimal(total) == Decimal("1.0"), "daily total must not exceed 1.0"
    finally:
        async with AsyncSession(_test_engine) as s:
            await s.execute(delete(LabourEntry).where(LabourEntry.worker_id == worker_id))
            await s.execute(delete(Worker).where(Worker.worker_id == worker_id))
            await s.execute(delete(Job).where(Job.job_id.in_([job1_id, job2_id])))
            await s.execute(delete(User).where(User.user_id == admin_id))
            await s.commit()
