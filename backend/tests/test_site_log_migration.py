"""WP A (A1): Alembic round-trip test for c7d8e9f0a1b2_add_site_log_tables.

Runs the real migration chain against a dedicated scratch database
(``sitetracker_migration_test``) so the main ``sitetracker_test`` DB —
whose schema conftest builds from metadata — is never touched:

    upgrade head → verify → downgrade b7e9f3a2d815 → verify gone →
    upgrade head → verify again

Alembic is invoked as a subprocess because ``alembic/env.py`` drives an
async engine via ``asyncio.run``, which cannot be nested inside the
pytest-asyncio event loop.
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
SCRATCH_DB = "sitetracker_migration_test"
SCRATCH_URL = (
    f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{SCRATCH_DB}"
)

SITE_LOG_TABLES = [
    "site_log_events",
    "site_log_event_revisions",
    "site_log_event_attachments",
    "site_log_event_audit_log",
    "capture_eligibility_transitions",
]
SITE_LOG_ENUMS = [
    "site_log_capture_status",
    "site_log_attachment_state",
    "capture_eligibility_state",
]


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
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
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
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        database=SCRATCH_DB,
    )
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _table_names() -> set[str]:
    rows = await _query(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    return {r["tablename"] for r in rows}


async def _enum_names() -> set[str]:
    rows = await _query(
        "SELECT typname FROM pg_type WHERE typtype = 'e'"
    )
    return {r["typname"] for r in rows}


async def _version() -> str:
    rows = await _query("SELECT version_num FROM alembic_version")
    assert len(rows) == 1
    return rows[0]["version_num"]


@pytest.mark.asyncio
async def test_site_log_migration_round_trip(scratch_db):
    # -- upgrade to head ---------------------------------------------------
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
    assert await _version() == "c7d8e9f0a1b2"

    tables = await _table_names()
    for t in SITE_LOG_TABLES:
        assert t in tables, f"missing table {t}"
    enums = await _enum_names()
    for e in SITE_LOG_ENUMS:
        assert e in enums, f"missing enum {e}"

    # Invariants live in the database, not just the ORM.
    uniques = {
        r["conname"]
        for r in await _query(
            "SELECT conname FROM pg_constraint WHERE contype = 'u'"
        )
    }
    for name in (
        "uq_slog_event_capture_client",
        "uq_slog_revision_no",
        "uq_slog_attachment_client",
        "uq_slog_eligibility_transition_no",
    ):
        assert name in uniques, f"missing UNIQUE {name}"

    checks = {
        r["conname"]
        for r in await _query(
            "SELECT conname FROM pg_constraint WHERE contype = 'c'"
        )
    }
    for name in (
        "ck_slog_revision_no_ge_1",
        "ck_slog_revision_withdrawn_reason",
        "ck_slog_revision_correction_reason",
        "ck_slog_attachment_media_type",
        "ck_slog_attachment_size_nonneg",
        "ck_slog_attachment_stored_has_evidence",
        "ck_slog_eligibility_no_ge_1",
        "ck_slog_eligibility_from_state",
    ):
        assert name in checks, f"missing CHECK {name}"

    partial_unique = await _query(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'uq_slog_attachment_evidence'"
    )
    assert len(partial_unique) == 1
    indexdef = partial_unique[0]["indexdef"]
    assert "UNIQUE" in indexdef and "evidence_id IS NOT NULL" in indexdef

    tenant_defaults = await _query(
        "SELECT table_name, column_default FROM information_schema.columns "
        "WHERE column_name = 'tenant_id' AND table_name = ANY($1::text[])",
        SITE_LOG_TABLES,
    )
    assert {r["table_name"] for r in tenant_defaults} == set(SITE_LOG_TABLES)
    assert all(
        "00000000-0000-0000-0000-000000000001" in r["column_default"]
        for r in tenant_defaults
    )

    # ---- FK / ON DELETE enumeration (retention proof) --------------------
    # confdeltype: a = NO ACTION, n = SET NULL, c = CASCADE, r = RESTRICT,
    # d = SET DEFAULT. Append-only history (revisions, audit, eligibility,
    # attachment provenance) must never cascade away, and the only SET
    # NULL permitted is the job link on the event itself (the evidence
    # precedent: the capture outlives a hard-deleted empty Job).
    fk_rows = await _query(
        "SELECT conrelid::regclass::text AS child, conname, "
        "       confrelid::regclass::text AS parent, confdeltype::text "
        "FROM pg_constraint "
        "WHERE contype = 'f' AND conrelid::regclass::text = ANY($1::text[]) "
        "ORDER BY child, conname",
        SITE_LOG_TABLES,
    )
    fks = {
        (r["child"], r["parent"]): r["confdeltype"] for r in fk_rows
    }
    expected_fks = {
        ("site_log_events", "users"): "a",
        ("site_log_events", "jobs"): "n",
        ("site_log_event_revisions", "site_log_events"): "a",
        ("site_log_event_revisions", "users"): "a",
        ("site_log_event_attachments", "site_log_events"): "a",
        ("site_log_event_attachments", "evidence"): "a",
        ("site_log_event_audit_log", "site_log_events"): "a",
        ("site_log_event_audit_log", "users"): "a",
        ("capture_eligibility_transitions", "site_log_events"): "a",
        ("capture_eligibility_transitions", "users"): "a",
    }
    assert fks == expected_fks, f"FK/ON DELETE mismatch: {fks}"
    assert "c" not in fks.values(), "CASCADE would erase append-only history"
    assert "d" not in fks.values()
    # Exactly one SET NULL: the event's job link, nothing history-bearing.
    assert [k for k, v in fks.items() if v == "n"] == [
        ("site_log_events", "jobs")
    ]

    # -- downgrade to the pre-WP-A head ------------------------------------
    down = _alembic("downgrade", "b7e9f3a2d815")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    assert await _version() == "b7e9f3a2d815"

    tables = await _table_names()
    for t in SITE_LOG_TABLES:
        assert t not in tables, f"table {t} survived downgrade"
    enums = await _enum_names()
    for e in SITE_LOG_ENUMS:
        assert e not in enums, f"enum {e} survived downgrade"
    # Pre-existing surfaces are untouched by the downgrade.
    assert "evidence" in tables and "jobs" in tables

    # -- upgrade again (round trip completes) ------------------------------
    up2 = _alembic("upgrade", "head")
    assert up2.returncode == 0, f"re-upgrade failed:\n{up2.stdout}\n{up2.stderr}"
    assert await _version() == "c7d8e9f0a1b2"
    tables = await _table_names()
    for t in SITE_LOG_TABLES:
        assert t in tables, f"missing table {t} after re-upgrade"
