"""Job Timeline PR 1 — model + migration tests.

Three concerns, mirroring ``tests/test_review_queue_model.py`` style:

1. **Migration round-trip smoke test.** Against a throwaway database,
   run ``alembic upgrade head`` -> ``downgrade -4`` -> ``upgrade head``
   and assert the four Timeline tables and four ENUM types appear, then
   disappear, then reappear. Isolated in its own scratch DB so it never
   touches the metadata-built ``sitetracker_test`` schema the other
   fixtures use.
2. **Insert/query one row per table.** ``job_checklist_items`` ->
   ``timeline_items`` -> ``timeline_attachments`` -> ``timeline_audit_log``.
3. **CHECK enforcement.** An ``issue`` row with a NULL ``status`` is
   rejected by ``ck_timeline_items_issue_requires_status``.

The round-trip test shells out to Alembic in a subprocess: Alembic's
online env runs its own ``asyncio.run(...)``, which cannot be nested
inside the session-scoped pytest event loop. It ``skip``s cleanly if
the DB / ``alembic`` binary is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    AttachmentUploadStatus,
    IssueStatus,
    Job,
    JobChecklistItem,
    JobStatus,
    TimelineAttachment,
    TimelineAuditLog,
    TimelineItem,
    TimelineItemType,
)

# Same host/port/creds as tests/conftest.py::TEST_DB_URL. The round-trip
# runs in its own scratch DB so it never collides with the suite's
# ``sitetracker_test`` schema.
_PG_HOST = "localhost"
_PG_PORT = 5433
_PG_USER = "sitetracker"
_PG_PASSWORD = "sitetracker"
_MAINT_DSN = (
    f"postgresql://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/postgres"
)
_SMOKE_DB = "sitetracker_timeline_smoke"
_BACKEND_DIR = Path(__file__).resolve().parents[1]

_NEW_TABLES = {
    "job_checklist_items",
    "timeline_items",
    "timeline_attachments",
    "timeline_audit_log",
}
_NEW_ENUMS = {
    "timeline_item_type",
    "issue_status",
    "issue_severity",
    "attachment_upload_status",
}


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #
async def _make_job(db_session, admin, *, name: str = "Kelly House") -> Job:
    """Fresh Job in the current transaction; mirrors other model tests."""
    job = Job(
        job_id=uuid.uuid4(),
        job_name=name,
        status=JobStatus.active,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


# --------------------------------------------------------------------------- #
# 1. Migration round-trip smoke test                                          #
# --------------------------------------------------------------------------- #
async def _scratch_names(scratch_dsn: str) -> tuple[set[str], set[str]]:
    """Return (public table names, enum type names) in the scratch DB."""
    conn = await asyncpg.connect(scratch_dsn)
    try:
        table_rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        enum_rows = await conn.fetch(
            "SELECT typname FROM pg_type WHERE typtype = 'e'"
        )
    finally:
        await conn.close()
    return (
        {r["tablename"] for r in table_rows},
        {r["typname"] for r in enum_rows},
    )


@pytest.mark.asyncio
async def test_timeline_migrations_round_trip():
    """upgrade head -> downgrade -4 -> upgrade head is clean and reversible."""
    alembic_bin = shutil.which("alembic")
    if alembic_bin is None:
        pytest.skip("alembic binary not on PATH")

    # Provision a throwaway database (skip if the server is unreachable).
    try:
        admin = await asyncpg.connect(_MAINT_DSN)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        pytest.skip(f"Postgres not reachable for migration smoke test: {exc}")
    try:
        # WITH (FORCE) terminates any lingering connections (PG13+).
        await admin.execute(f"DROP DATABASE IF EXISTS {_SMOKE_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {_SMOKE_DB} OWNER {_PG_USER}")
    finally:
        await admin.close()

    scratch_dsn = (
        f"postgresql://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_SMOKE_DB}"
    )
    env = {
        **os.environ,
        "APP_ENV": "development",  # avoid the non-dev fail-fast gates
        "DATABASE_URL": (
            f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{_SMOKE_DB}"
        ),
        "JWT_SECRET": "test-secret-for-timeline-migration-roundtrip-0000",
        "CORS_ALLOWED_ORIGINS": "https://localhost.test",
    }

    def _alembic(*args: str) -> None:
        result = subprocess.run(
            [alembic_bin, *args],
            cwd=_BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"`alembic {' '.join(args)}` failed:\n{result.stdout}\n{result.stderr}"
        )

    try:
        _alembic("upgrade", "head")
        tables, enums = await _scratch_names(scratch_dsn)
        assert tables >= _NEW_TABLES, f"missing tables after upgrade: {tables}"
        assert enums >= _NEW_ENUMS, f"missing enums after upgrade: {enums}"

        _alembic("downgrade", "-4")
        tables, enums = await _scratch_names(scratch_dsn)
        assert not (_NEW_TABLES & tables), f"tables survived downgrade: {tables}"
        assert not (_NEW_ENUMS & enums), f"enums survived downgrade: {enums}"

        _alembic("upgrade", "head")
        tables, enums = await _scratch_names(scratch_dsn)
        assert tables >= _NEW_TABLES, f"missing tables after re-upgrade: {tables}"
        assert enums >= _NEW_ENUMS, f"missing enums after re-upgrade: {enums}"
    finally:
        admin = await asyncpg.connect(_MAINT_DSN)
        try:
            await admin.execute(
                f"DROP DATABASE IF EXISTS {_SMOKE_DB} WITH (FORCE)"
            )
        finally:
            await admin.close()


# --------------------------------------------------------------------------- #
# 2. Insert / query one row per table                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_checklist_item_round_trip(db_session, seeded_admin):
    """A ``job_checklist_items`` row inserts and reads back with its defaults."""
    job = await _make_job(db_session, seeded_admin)

    item = JobChecklistItem(
        checklist_item_id=uuid.uuid4(),
        job_id=job.job_id,
        label="flood test",
        phase="Waterproofing",
    )
    db_session.add(item)
    await db_session.flush()
    await db_session.refresh(item)

    assert item.sort_order == 0
    assert item.is_done is False
    assert item.requires_evidence is False
    assert item.created_at is not None
    assert item.deleted_at is None


@pytest.mark.asyncio
async def test_timeline_item_round_trip_daily_note(db_session, seeded_admin):
    """A ``daily_note`` item (no issue fields) inserts and reads back."""
    job = await _make_job(db_session, seeded_admin)
    checklist = JobChecklistItem(
        checklist_item_id=uuid.uuid4(), job_id=job.job_id, label="pour slab"
    )
    db_session.add(checklist)
    await db_session.flush()

    item = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=job.job_id,
        item_type=TimelineItemType.daily_note,
        body="Concrete delivered, slab poured.",
        checklist_item_id=checklist.checklist_item_id,
        occurred_at=datetime(2026, 7, 6, 9, 30, tzinfo=UTC),
        created_by=seeded_admin.user_id,
    )
    db_session.add(item)
    await db_session.flush()

    reloaded = (
        await db_session.execute(
            select(TimelineItem).where(
                TimelineItem.timeline_item_id == item.timeline_item_id
            )
        )
    ).scalar_one()

    assert reloaded.item_type is TimelineItemType.daily_note
    assert reloaded.status is None
    assert reloaded.severity is None
    assert reloaded.checklist_item_id == checklist.checklist_item_id
    assert reloaded.requires_evidence is False
    assert reloaded.created_at is not None
    assert reloaded.updated_at is not None


@pytest.mark.asyncio
async def test_timeline_item_round_trip_issue_with_status(
    db_session, seeded_admin
):
    """An ``issue`` item carrying a ``status`` satisfies the CHECK and reads back."""
    job = await _make_job(db_session, seeded_admin)

    item = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=job.job_id,
        item_type=TimelineItemType.issue,
        title="Leaking pipe in bathroom",
        status=IssueStatus.open,
        occurred_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        created_by=seeded_admin.user_id,
    )
    db_session.add(item)
    await db_session.flush()
    await db_session.refresh(item)

    assert item.status is IssueStatus.open


@pytest.mark.asyncio
async def test_attachment_round_trip(db_session, seeded_admin):
    """A ``timeline_attachments`` row inserts with evidence metadata + defaults."""
    job = await _make_job(db_session, seeded_admin)
    item = TimelineItem(
        timeline_item_id=uuid.uuid4(),
        job_id=job.job_id,
        item_type=TimelineItemType.photo,
        occurred_at=datetime(2026, 7, 6, 11, 0, tzinfo=UTC),
        created_by=seeded_admin.user_id,
    )
    db_session.add(item)
    await db_session.flush()

    attachment = TimelineAttachment(
        attachment_id=uuid.uuid4(),
        timeline_item_id=item.timeline_item_id,
        storage_key="jobs/kelly/2026/07/06/abc.jpg",
        content_type="image/jpeg",
        byte_size=384_000,
        width=1600,
        height=1200,
        taken_at=datetime(2026, 7, 6, 10, 52, 38, tzinfo=UTC),
        gps_lat=-33.8688,
        gps_lng=151.2093,
        created_by=seeded_admin.user_id,
    )
    db_session.add(attachment)
    await db_session.flush()
    await db_session.refresh(attachment)

    assert attachment.upload_status is AttachmentUploadStatus.pending
    assert attachment.gps_lat == pytest.approx(-33.8688)
    assert attachment.gps_lng == pytest.approx(151.2093)
    assert attachment.deleted_at is None


@pytest.mark.asyncio
async def test_audit_log_round_trip(db_session, seeded_admin):
    """A ``timeline_audit_log`` row round-trips its JSONB ``detail``.

    ``timeline_item_id`` is deliberately populated with a value that has
    no matching row — the table has no hard FK, so this must succeed.
    """
    job = await _make_job(db_session, seeded_admin)
    orphan_item_id = uuid.uuid4()

    entry = TimelineAuditLog(
        audit_id=uuid.uuid4(),
        timeline_item_id=orphan_item_id,
        job_id=job.job_id,
        action="status_change",
        actor_user_id=seeded_admin.user_id,
        detail={"status": {"old": "open", "new": "resolved"}},
    )
    db_session.add(entry)
    await db_session.flush()

    reloaded = (
        await db_session.execute(
            select(TimelineAuditLog).where(
                TimelineAuditLog.audit_id == entry.audit_id
            )
        )
    ).scalar_one()

    assert reloaded.timeline_item_id == orphan_item_id
    assert reloaded.action == "status_change"
    assert reloaded.detail == {"status": {"old": "open", "new": "resolved"}}
    assert reloaded.created_at is not None


# --------------------------------------------------------------------------- #
# 3. CHECK enforcement                                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_issue_without_status_rejected_by_check(db_session, seeded_admin):
    """An ``issue`` item with NULL ``status`` violates the CHECK constraint."""
    job = await _make_job(db_session, seeded_admin)

    # Raw INSERT so we hit Postgres with the exact shape the CHECK must
    # reject (bypasses any application-side coercion). Wrapped in a
    # SAVEPOINT so the IntegrityError stays local to this block and the
    # fixture's outer transaction rolls back cleanly.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                sa.text(
                    """
                    INSERT INTO timeline_items
                        (timeline_item_id, job_id, item_type, occurred_at,
                         created_by, created_at, updated_at)
                    VALUES
                        (:tid, :jid, 'issue', now(), :uid, now(), now())
                    """
                ),
                {
                    "tid": uuid.uuid4(),
                    "jid": job.job_id,
                    "uid": seeded_admin.user_id,
                },
            )
