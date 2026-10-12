"""Site Log → Task: Alembic round trip for ``e1f2a3b4c5d6`` (plan v2 §7, §9).

Runs against its own scratch database so neither ``sitetracker_test`` nor
the Site Log migration test's scratch database is touched:

    upgrade head → tasks tables, types, constraints, FKs present →
    downgrade d9e0f1a2b3c4 → task tables and types gone, Site Log intact →
    upgrade head → present again

The downgrade is exercised here, on a disposable database, because it must
work. It is NOT a rollback step: on a real database it deletes every task
(see the migration's docstring).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_HOST, PG_PORT = "localhost", 5433
PG_USER = PG_PASS = "sitetracker"
SCRATCH_DB = "sitetracker_task_migration_test"
SCRATCH_URL = (
    f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{SCRATCH_DB}"
)
TASK_REVISION = "e1f2a3b4c5d6"
PREVIOUS_REVISION = "d9e0f1a2b3c4"
TASK_TABLES = ["tasks", "task_audit_log"]
TASK_ENUMS = ["task_status", "task_origin"]


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = SCRATCH_URL
    env.pop("ENVIRONMENT", None)
    env.setdefault("APP_ENV", "test")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


async def _admin_conn() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
        database="sitetracker_test",
    )


@pytest_asyncio.fixture
async def scratch_db():
    conn = await _admin_conn()
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await conn.close()
    yield
    conn = await _admin_conn()
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    finally:
        await conn.close()


async def _query(sql: str, *args):
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
        database=SCRATCH_DB,
    )
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _tables() -> set[str]:
    rows = await _query("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r["tablename"] for r in rows}


async def _enums() -> set[str]:
    rows = await _query("SELECT typname FROM pg_type WHERE typtype = 'e'")
    return {r["typname"] for r in rows}


async def _version() -> str:
    rows = await _query("SELECT version_num FROM alembic_version")
    assert len(rows) == 1
    return rows[0]["version_num"]


async def _assert_task_schema() -> None:
    tables = await _tables()
    for t in TASK_TABLES:
        assert t in tables, f"missing table {t}"
    enums = await _enums()
    for e in TASK_ENUMS:
        assert e in enums, f"missing enum {e}"

    labels = await _query(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'task_status' ORDER BY e.enumsortorder"
    )
    assert [r["enumlabel"] for r in labels] == ["to_do", "in_progress", "blocked", "done"]

    uniques = {
        r["conname"]
        for r in await _query("SELECT conname FROM pg_constraint WHERE contype = 'u'")
    }
    assert "uq_task_create_client" in uniques
    checks = {
        r["conname"]
        for r in await _query("SELECT conname FROM pg_constraint WHERE contype = 'c'")
    }
    assert {"ck_task_title_not_blank", "ck_task_source_revision_no_ge_1"} <= checks

    indexes = {
        r["indexname"]
        for r in await _query(
            "SELECT indexname FROM pg_indexes WHERE tablename = ANY($1::text[])",
            TASK_TABLES,
        )
    }
    assert {
        "ix_task_job_status", "ix_task_assignee_status", "ix_task_source_event",
        "ix_task_creator_created", "ix_task_audit_task_created",
    } <= indexes

    # Every FK is NO ACTION: tasks and their history are never cascaded away,
    # and a job, entry or user a task refers to cannot be hard-deleted.
    fk_rows = await _query(
        "SELECT conrelid::regclass::text AS child, conname, "
        "       confrelid::regclass::text AS parent, confdeltype::text "
        "FROM pg_constraint "
        "WHERE contype = 'f' AND conrelid::regclass::text = ANY($1::text[])",
        TASK_TABLES,
    )
    fks = sorted((r["child"], r["parent"], r["confdeltype"]) for r in fk_rows)
    assert fks == sorted([
        ("tasks", "jobs", "a"),
        ("tasks", "site_log_events", "a"),
        ("tasks", "users", "a"),  # assignee
        ("tasks", "users", "a"),  # creator
        ("task_audit_log", "tasks", "a"),
        ("task_audit_log", "users", "a"),
    ]), fks

    tenant_defaults = await _query(
        "SELECT table_name, column_default FROM information_schema.columns "
        "WHERE column_name = 'tenant_id' AND table_name = ANY($1::text[])",
        TASK_TABLES,
    )
    assert {r["table_name"] for r in tenant_defaults} == set(TASK_TABLES)
    assert all(
        "00000000-0000-0000-0000-000000000001" in r["column_default"]
        for r in tenant_defaults
    )


@pytest.mark.asyncio
async def test_task_migration_round_trip(scratch_db):
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
    assert await _version() == TASK_REVISION
    await _assert_task_schema()

    down = _alembic("downgrade", PREVIOUS_REVISION)
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    assert await _version() == PREVIOUS_REVISION
    tables = await _tables()
    for t in TASK_TABLES:
        assert t not in tables, f"table {t} survived downgrade"
    enums = await _enums()
    for e in TASK_ENUMS:
        assert e not in enums, f"enum {e} survived downgrade"
    # The Site Log tables the tasks referred to are untouched.
    assert {"site_log_events", "site_log_event_revisions", "jobs", "users"} <= tables

    up2 = _alembic("upgrade", "head")
    assert up2.returncode == 0, f"re-upgrade failed:\n{up2.stdout}\n{up2.stderr}"
    assert await _version() == TASK_REVISION
    await _assert_task_schema()
