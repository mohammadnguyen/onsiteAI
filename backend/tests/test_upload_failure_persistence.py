"""What the caller may claim when the failure bookkeeping itself fails.

CONTROLLED FAULT INJECTION, not a network experiment. Nothing here
disconnects a socket or kills a process: a wrapper around the session's
``commit`` raises either BEFORE delegating to the real commit, or AFTER the
real commit returned successfully. The second shape stands in for a lost
success acknowledgement — the database committed, the caller never learned
it. Real transport loss can produce the same two outcomes; this module
pins the SERVICE behaviour for both, it does not reproduce the transport.

Why a scratch database: the shared rollback harness runs each test inside
one transaction that is discarded, so "the row after a real commit" cannot
be observed there. This module builds ``sitetracker_persistence_test`` with
``alembic upgrade head`` (the pattern the migration and concurrency suites
already use) and reads the final state back over an INDEPENDENT pooled
connection, so what it asserts is what the database actually kept.

The contract under test: when the bookkeeping commit's outcome is unknown,
neither service may claim a durable ``failed`` NOR claim the row is still
``pending``. Both must report the persistence outcome as unconfirmed, keep
propagating the original upload exception, and attempt no retry, reset or
further state transition. Which outcome actually happened differs between
the two fault shapes, and the tests assert both — the diagnosis text stays
the same because the service cannot tell them apart.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models import (
    AttachmentState,
    Evidence,
    EvidenceAuditLog,
    EvidenceStatus,
    SiteLogEventAttachment,
)
from app.models.user import LanguageCode, User, UserRole
from app.services import evidence as evidence_service
from app.services import site_log as svc
from app.services.evidence_storage import LocalEvidenceStorage

pytestmark = pytest.mark.asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_HOST, PG_PORT = "localhost", 5433
PG_USER = PG_PASS = "sitetracker"
# One database per fixture invocation: two overlapping runs of this module
# must not drop or collide with each other's schema (Codex round 7).
SCRATCH_DB_PREFIX = "sitetracker_persistence_test"
MAX_BYTES = 1024 * 1024


def _scratch_url(db_name: str) -> str:
    return f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{db_name}"

UNCONFIRMED = "persistence UNCONFIRMED"
# The one sanctioned way to mention an outcome: an explicit disjunction that
# resolves to "read the row". These are matched literally — an earlier
# version of this guard ended its pattern with a greedy tail, which silently
# swallowed anything appended after the disjunction and would have accepted
# a note that went on to claim an outcome (Codex round 6).
SANCTIONED_DISJUNCTIONS = (
    "the attempt is either failed or still pending — read the row to determine which",
    "the row is either failed or still pending — read it to determine which",
)
# The note is validated as a WHOLE against a per-service template: the
# metadata block is constrained to identifier fields, so no free text can
# hide a claim there, and nothing may follow the disjunction. The claim scan
# below is the second line of defence against a reworded diagnosis.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CLASS = r"[A-Za-z_][A-Za-z0-9_]*"
NOTE_TEMPLATES = (
    re.compile(
        r"^site_log failed transition persistence UNCONFIRMED "
        rf"\(event_id={_UUID} attachment_id={_UUID} attempt_no=[1-9][0-9]* "
        rf"reason=internal_error\): {_CLASS}; "
        + re.escape(SANCTIONED_DISJUNCTIONS[0])
        + r"$"
    ),
    re.compile(
        r"^evidence failed transition persistence UNCONFIRMED "
        rf"\(evidence_id={_UUID}\): {_CLASS}; "
        + re.escape(SANCTIONED_DISJUNCTIONS[1])
        + r"$"
    ),
)
LOG_SUFFIX = re.compile(rf"^ upload_error={_CLASS}$")
SERVICE_LOGGERS = ("app.services.site_log", "app.services.evidence")
SOURCE_LOG_TEMPLATES = (
    re.compile(
        rf"^site_log upload source failure event_id={_UUID} "
        rf"attachment_id={_UUID} attempt_no=[1-9][0-9]* error={_CLASS}$"
    ),
    re.compile(
        rf"^evidence upload failed \(source\) evidence_id={_UUID} error={_CLASS}$"
    ),
)
# Wordings the diagnosis must never use: each asserts an outcome the
# service cannot know at that point.
FORBIDDEN_CLAIMS = (
    "NOT persisted",
    "not persisted",
    "still pending",
    "stays pending",
    "remains pending",
    "was not written",
    "durable failure",
)


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env.pop("ENVIRONMENT", None)
    env.setdefault("APP_ENV", "test")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=300,
    )


async def _admin_conn():
    return await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
        database="sitetracker_test",
    )


@pytest.fixture(scope="module")
async def engine():
    db_name = f"{SCRATCH_DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    url = _scratch_url(db_name)
    conn = await _admin_conn()
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    eng = None
    try:
        # Inside the try: a migration failure or timeout must still drop the
        # database this invocation owns.
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
        eng = create_async_engine(url, pool_size=6, max_overflow=4)
        yield eng
    finally:
        if eng is not None:
            await eng.dispose()
        conn = await _admin_conn()
        try:
            # FORCE (PG 13+) also evicts a connection left behind by a
            # failed test, so the drop cannot leak the database.
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await conn.close()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def uploader(factory) -> User:
    tag = uuid.uuid4().hex[:8]
    async with factory() as s:
        user = User(
            user_id=uuid.uuid4(), full_name="U", email=f"u-{tag}@example.com",
            password_hash=hash_password("x"), role=UserRole.admin,
            language_preference=LanguageCode.en, is_active=True,
        )
        s.add(user)
        await s.commit()
        return user


@pytest.fixture
def storage(tmp_path):
    return LocalEvidenceStorage(tmp_path)


async def _source_os_error():
    yield b"first"
    raise OSError(5, "Input/output error")


class _CommitFault:
    """Fault injector for exactly one commit of one session.

    ``when="before"`` raises instead of committing (nothing is persisted).
    ``when="after"`` awaits the real commit, lets the database keep the
    write, and only then raises — the lost-acknowledgement shape.
    """

    def __init__(self, session: AsyncSession, *, nth: int, when: str):
        self._session = session
        self._nth = nth
        self._when = when
        self._real = type(session).commit
        self._calls = 0
        self.fired = False

    def install(self, monkeypatch):
        fault = self

        async def commit(self):
            if self is not fault._session:
                return await fault._real(self)
            fault._calls += 1
            if fault._calls != fault._nth:
                return await fault._real(self)
            fault.fired = True
            if fault._when == "before":
                raise RuntimeError("injected: commit never reached the database")
            await fault._real(self)
            raise RuntimeError("injected: commit succeeded, acknowledgement lost")

        monkeypatch.setattr(type(self._session), "commit", commit)


def _notes(exc: BaseException) -> list[str]:
    return list(getattr(exc, "__notes__", []))


def _strip_sanctioned(text: str) -> str:
    for disjunction in SANCTIONED_DISJUNCTIONS:
        text = text.replace(disjunction, "")
    return text


def _assert_unconfirmed(exc: BaseException, caplog) -> None:
    """The diagnosis names the uncertainty and claims neither outcome.

    The whole diagnosis is asserted, not just the part carrying the marker:
    an earlier version inspected only text containing ``UNCONFIRMED``, so a
    SECOND note or error record asserting an outcome slipped past whenever
    its wording was outside the forbidden list (Codex round 8). Exactly one
    note and exactly two service error records may exist.
    """
    notes = _notes(exc)
    assert len(notes) == 1, notes
    note = notes[0]
    assert UNCONFIRMED in note, note
    # Whole-note match against a constrained template: no free text in the
    # metadata block, nothing before or after the sanctioned disjunction.
    assert any(t.match(note) for t in NOTE_TEMPLATES), note

    records = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and r.name in SERVICE_LOGGERS
    ]
    messages = [r.getMessage() for r in records]
    # The service's own upload-failure line, then the diagnosis. Nothing else.
    assert len(messages) == 2, messages
    assert any(t.match(messages[0]) for t in SOURCE_LOG_TEMPLATES), messages[0]
    assert messages[1].startswith(note), (messages[1], note)
    assert LOG_SUFFIX.match(messages[1][len(note):]), messages[1]

    # Second line of defence: outside the sanctioned disjunction, no text in
    # the note or in any service error record may assert an outcome.
    scanned = [_strip_sanctioned(t) for t in [note, *messages]]
    for claim in FORBIDDEN_CLAIMS:
        assert not any(claim in t for t in scanned), (claim, scanned)


# ------------------------------------------------------------ verification
# Always over a NEW session on its own pooled connection.


async def _final_attachment(factory, event_id, cid):
    async with factory() as s:
        att = (
            await s.execute(
                select(SiteLogEventAttachment).where(
                    SiteLogEventAttachment.site_log_event_id == event_id,
                    SiteLogEventAttachment.attachment_client_id == cid,
                )
            )
        ).scalar_one()
        ev = await s.get(Evidence, att.evidence_id)
        actions = list(
            (
                await s.execute(
                    select(EvidenceAuditLog.action)
                    .where(EvidenceAuditLog.evidence_id == att.evidence_id)
                    .order_by(EvidenceAuditLog.created_at, EvidenceAuditLog.audit_id)
                )
            ).scalars().all()
        )
        return att.state, att.upload_attempt_no, ev.status, actions


async def _final_evidence(factory, uploader_id):
    async with factory() as s:
        ev = (
            await s.execute(
                select(Evidence).where(Evidence.uploaded_by_user_id == uploader_id)
            )
        ).scalar_one()
        actions = list(
            (
                await s.execute(
                    select(EvidenceAuditLog.action)
                    .where(EvidenceAuditLog.evidence_id == ev.evidence_id)
                    .order_by(EvidenceAuditLog.created_at, EvidenceAuditLog.audit_id)
                )
            ).scalars().all()
        )
        return ev.status, ev.storage_key, actions


async def _declare_one(session, storage, factory, user):
    cid = uuid.uuid4()
    res = await svc.declare_capture(
        session, storage, factory, user=user,
        capture_client_id=uuid.uuid4(), job_id=None, occurred_at=None,
        internal_location=None, body_text=None,
        attachments=[{
            "attachment_client_id": cid,
            "declared_media_type": "audio",
            "declared_size_bytes": None,
        }],
        max_bytes=MAX_BYTES,
    )
    return res.view.event.site_log_event_id, cid


# --------------------------------------------------------------- Site Log


async def test_site_log_bookkeeping_fails_before_commit_row_is_still_pending(
    factory, uploader, storage, monkeypatch, caplog
):
    """Fault BEFORE the real commit: the database kept nothing, so the row
    really is still pending — but the service said only 'unconfirmed'."""
    async with factory() as declare_session:
        eid, cid = await _declare_one(declare_session, storage, factory, uploader)

    async with factory() as session:
        # commit 1 = Txn A (acquire); commit 2 = the failure bookkeeping.
        fault = _CommitFault(session, nth=2, when="before")
        fault.install(monkeypatch)
        with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
            await svc.upload_attachment(
                session, storage, factory, user=uploader, event_id=eid,
                attachment_client_id=cid, mime_type="audio/m4a",
                chunks=_source_os_error(), max_bytes=MAX_BYTES,
            )
    assert fault.fired
    assert info.value.errno == 5
    _assert_unconfirmed(info.value, caplog)

    state, attempt, ev_status, actions = await _final_attachment(factory, eid, cid)
    assert (state, attempt) == (AttachmentState.pending, 1)
    assert ev_status is EvidenceStatus.pending
    assert actions == ["uploaded"]


async def test_site_log_bookkeeping_commit_lands_but_ack_is_lost_row_is_failed(
    factory, uploader, storage, monkeypatch, caplog
):
    """Fault AFTER the real commit: the row IS failed. The diagnosis is the
    same text, which is exactly why it must not claim 'still pending'."""
    async with factory() as declare_session:
        eid, cid = await _declare_one(declare_session, storage, factory, uploader)

    async with factory() as session:
        fault = _CommitFault(session, nth=2, when="after")
        fault.install(monkeypatch)
        with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
            await svc.upload_attachment(
                session, storage, factory, user=uploader, event_id=eid,
                attachment_client_id=cid, mime_type="audio/m4a",
                chunks=_source_os_error(), max_bytes=MAX_BYTES,
            )
    assert fault.fired
    assert info.value.errno == 5
    _assert_unconfirmed(info.value, caplog)

    state, attempt, ev_status, actions = await _final_attachment(factory, eid, cid)
    assert (state, attempt) == (AttachmentState.failed, 1)
    assert ev_status is EvidenceStatus.failed
    assert actions == ["uploaded", "failed"]


async def test_site_log_no_extra_transition_after_an_unconfirmed_bookkeeping(
    factory, uploader, storage, monkeypatch, caplog
):
    """No retry, no reset, no second transition: exactly one failed audit
    exists after the lost acknowledgement, written by the commit that
    landed."""
    async with factory() as declare_session:
        eid, cid = await _declare_one(declare_session, storage, factory, uploader)

    async with factory() as session:
        fault = _CommitFault(session, nth=2, when="after")
        fault.install(monkeypatch)
        with caplog.at_level("ERROR"), pytest.raises(OSError):
            await svc.upload_attachment(
                session, storage, factory, user=uploader, event_id=eid,
                attachment_client_id=cid, mime_type="audio/m4a",
                chunks=_source_os_error(), max_bytes=MAX_BYTES,
            )

    _, _, _, actions = await _final_attachment(factory, eid, cid)
    assert actions.count("failed") == 1


# -------------------------------------------------------- legacy /evidence


async def test_legacy_bookkeeping_fails_before_commit_row_is_still_pending(
    factory, uploader, storage, monkeypatch, caplog
):
    async with factory() as session:
        # commit 1 = the pending row; commit 2 = the failure bookkeeping.
        fault = _CommitFault(session, nth=2, when="before")
        fault.install(monkeypatch)
        with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
            await evidence_service.create_evidence(
                session, storage, uploader=uploader, chunks=_source_os_error(),
                mime_type="audio/m4a", occurred_at=None, original_filename=None,
                job_id=None, max_bytes=MAX_BYTES,
            )
    assert fault.fired
    assert info.value.errno == 5
    _assert_unconfirmed(info.value, caplog)

    status, key, actions = await _final_evidence(factory, uploader.user_id)
    assert status is EvidenceStatus.pending
    assert key is None
    assert actions == ["uploaded"]


async def test_legacy_bookkeeping_commit_lands_but_ack_is_lost_row_is_failed(
    factory, uploader, storage, monkeypatch, caplog
):
    async with factory() as session:
        fault = _CommitFault(session, nth=2, when="after")
        fault.install(monkeypatch)
        with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
            await evidence_service.create_evidence(
                session, storage, uploader=uploader, chunks=_source_os_error(),
                mime_type="audio/m4a", occurred_at=None, original_filename=None,
                job_id=None, max_bytes=MAX_BYTES,
            )
    assert fault.fired
    assert info.value.errno == 5
    _assert_unconfirmed(info.value, caplog)

    status, key, actions = await _final_evidence(factory, uploader.user_id)
    assert status is EvidenceStatus.failed
    assert key is None
    assert actions == ["uploaded", "failed"]


async def test_both_fault_shapes_produce_the_same_diagnosis(
    factory, uploader, storage, monkeypatch, caplog
):
    """The point of the contract: the service cannot tell the two apart, so
    the note it attaches is identical while the durable outcomes differ."""
    seen = {}
    for when in ("before", "after"):
        caplog.clear()
        async with factory() as session:
            fault = _CommitFault(session, nth=2, when=when)
            fault.install(monkeypatch)
            with caplog.at_level("ERROR"), pytest.raises(OSError) as info:
                await evidence_service.create_evidence(
                    session, storage, uploader=uploader, chunks=_source_os_error(),
                    mime_type="audio/m4a", occurred_at=None, original_filename=None,
                    job_id=None, max_bytes=MAX_BYTES,
                )
            monkeypatch.undo()
        note = next(n for n in _notes(info.value) if UNCONFIRMED in n)
        # Drop the evidence id, which necessarily differs between the runs.
        seen[when] = note.split("(evidence_id=")[0] + note.split("):", 1)[1]

    assert seen["before"] == seen["after"]
    statuses = set()
    async with factory() as s:
        for ev in (
            await s.execute(
                select(Evidence).where(Evidence.uploaded_by_user_id == uploader.user_id)
            )
        ).scalars().all():
            statuses.add(ev.status)
    assert statuses == {EvidenceStatus.pending, EvidenceStatus.failed}


# ------------------------------------------------- the guard guards itself


class _FakeRecord:
    def __init__(self, message: str, *, name: str = "app.services.evidence"):
        self._message = message
        self.name = name
        self.levelno = logging.ERROR

    def getMessage(self) -> str:
        return self._message


class _FakeCaplog:
    def __init__(self, *messages, name: str = "app.services.evidence"):
        self.records = [
            m if isinstance(m, _FakeRecord) else _FakeRecord(m, name=name)
            for m in messages
        ]


EVIDENCE_SOURCE_LOG = (
    "evidence upload failed (source) "
    "evidence_id=00000000-0000-0000-0000-000000000000 error=OSError"
)
SITE_LOG_SOURCE_LOG = (
    "site_log upload source failure "
    "event_id=00000000-0000-0000-0000-000000000000 "
    "attachment_id=11111111-1111-1111-1111-111111111111 "
    "attempt_no=1 error=OSError"
)


def _caplog_for(note: str, *extra, name: str = "app.services.evidence") -> _FakeCaplog:
    """The two records the evidence service really emits, plus anything the
    test wants to add."""
    return _FakeCaplog(
        EVIDENCE_SOURCE_LOG, note + " upload_error=OSError", *extra, name=name
    )


def _diagnosis(disjunction: str, *, suffix: str = "", metadata: str | None = None) -> str:
    inner = metadata or "evidence_id=00000000-0000-0000-0000-000000000000"
    return (
        "evidence failed transition persistence UNCONFIRMED "
        f"({inner}): RuntimeError; {disjunction}{suffix}"
    )


async def test_guard_accepts_exactly_the_sanctioned_diagnosis():
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    _assert_unconfirmed(exc, _caplog_for(note))


@pytest.mark.parametrize(
    "suffix",
    [
        "; failed transition NOT persisted, row stays pending",
        " (the row is still pending)",
        " — no durable failure was written",
    ],
)
async def test_guard_rejects_a_claim_appended_after_the_disjunction(suffix):
    """An earlier guard ended its pattern with a greedy tail and silently
    swallowed anything after the sanctioned disjunction, so a note that went
    on to assert an outcome passed (Codex round 6). It must not."""
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1], suffix=suffix)
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _caplog_for(note))


async def test_guard_rejects_a_claim_only_present_in_the_log_line():
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(
            exc,
            _FakeCaplog(
                EVIDENCE_SOURCE_LOG,
                note + " upload_error=OSError; row stays pending",
            ),
        )


async def test_guard_rejects_a_diagnosis_that_asserts_instead_of_disjoining():
    note = (
        "evidence failed transition persistence UNCONFIRMED "
        "(evidence_id=00000000-0000-0000-0000-000000000000): RuntimeError; "
        "the row is still pending"
    )
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _caplog_for(note))


@pytest.mark.parametrize(
    "metadata",
    [
        "the failed transition was committed; "
        "evidence_id=00000000-0000-0000-0000-000000000000",
        "evidence_id=00000000-0000-0000-0000-000000000000, write confirmed",
        "evidence_id=not-a-uuid",
    ],
)
async def test_guard_rejects_a_claim_hidden_in_the_metadata_block(metadata):
    """Free text inside the parentheses could carry an assertion the
    forbidden-claim list does not know (Codex round 7). Only the
    constrained template catches these, so they are mutation coverage for
    the structural validation itself."""
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1], metadata=metadata)
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _caplog_for(note))


async def test_guard_rejects_an_unlisted_claim_appended_to_the_note():
    """Wording outside FORBIDDEN_CLAIMS: only the whole-note template can
    reject it."""
    note = _diagnosis(
        SANCTIONED_DISJUNCTIONS[1], suffix="; the failed transition was committed"
    )
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _caplog_for(note))


async def test_guard_rejects_an_unlisted_claim_appended_to_the_log_line():
    """Same wording, this time only in the log line: only LOG_SUFFIX can
    reject it."""
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(
            exc,
            _FakeCaplog(
                EVIDENCE_SOURCE_LOG,
                note + " upload_error=OSError; the failed transition was committed",
            ),
        )


async def test_guard_accepts_the_real_site_log_note_shape():
    """The site-log template is exercised too, not only the evidence one."""
    note = (
        "site_log failed transition persistence UNCONFIRMED "
        "(event_id=00000000-0000-0000-0000-000000000000 "
        "attachment_id=11111111-1111-1111-1111-111111111111 "
        "attempt_no=1 reason=internal_error): RuntimeError; "
        + SANCTIONED_DISJUNCTIONS[0]
    )
    exc = RuntimeError("original")
    exc.add_note(note)
    _assert_unconfirmed(
        exc,
        _FakeCaplog(
            SITE_LOG_SOURCE_LOG,
            note + " upload_error=OSError",
            name="app.services.site_log",
        ),
    )


async def test_guard_rejects_a_second_note_that_asserts_an_outcome():
    """An extra note is not covered by the marker-based checks, so only the
    completeness assertion can reject it (Codex round 8)."""
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    exc.add_note("The failed transition was committed; the database write is confirmed.")
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _caplog_for(note))


async def test_guard_rejects_an_extra_error_record_that_asserts_an_outcome():
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(
            exc,
            _caplog_for(
                note,
                "The failed transition was committed; the database write is confirmed.",
            ),
        )


async def test_guard_rejects_a_missing_service_upload_failure_record():
    """The complete expected output includes the service's own
    upload-failure line; dropping it must not pass unnoticed."""
    note = _diagnosis(SANCTIONED_DISJUNCTIONS[1])
    exc = RuntimeError("original")
    exc.add_note(note)
    with pytest.raises(AssertionError):
        _assert_unconfirmed(exc, _FakeCaplog(note + " upload_error=OSError"))
