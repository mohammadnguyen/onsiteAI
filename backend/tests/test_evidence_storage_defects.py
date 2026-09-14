"""WP-S-core fail-first defect tests (F1).

Every test here imports ONLY adapter names that exist on the recorded base
(origin/main 723bb6e) and asserts the DEFECT, so this file plus the shared
fake ``tests/support/s3_fake.py`` (copied alongside it; it depends on
botocore, which the pinned aioboto3 dependency installs) run unchanged
against a scratch checkout of the base — where each test must fail at its
defect assertion or at the raw escape of the defect exception (never at an
import, fixture or route error) — and against the fixed adapter, where each
must pass. The base run log is attached to the PR.

Defects covered (design-supplement-v4 §A3 / §A8, tracked R7 / R8):

* WP-S(1) error classification — on the base every HEAD exception is
  "key free" and every ``exists`` exception is ``False``; every GET
  acquisition error is ``ObjectNotFound``; client-entry and response-stream
  failures escape raw.
* WP-S(4) R7 — the S3 adapter wraps exceptions raised by the chunk SOURCE
  (``SiteLogTooLarge`` / ``EvidenceTooLarge``) into ``EvidenceStorageError``;
  R8 — the local adapter leaves the ``.part`` file behind for any source
  exception that is not an ``OSError`` and for cancellation; the S3 adapter
  leaves the multipart upload open on cancellation.
* FD1 mechanism — on attempt-free keys a stale attempt that publishes after
  a newer attempt confirmed overwrites the newer object (identity change
  with identical bytes; content damage under a shared key prefix).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from botocore.exceptions import NoCredentialsError, ReadTimeoutError

from app.services import evidence_storage as es
from app.services.evidence import EvidenceTooLarge
from app.services.evidence_storage import (
    EvidenceStorageError,
    LocalEvidenceStorage,
    ObjectNotFound,
    local_staging_name,
    s3_staging_key,
)
from app.services.site_log import SiteLogTooLarge
from tests.support.s3_fake import (
    FakeObject,
    FakeS3State,
    client_error,
    fake_s3_storage,
    ops,
    until,
)

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes):
    yield payload


# --------------------------------------------------------- WP-S(1) defects


async def test_s3_head_forbidden_never_copies():
    """403 on the pre-copy HEAD must fail the put, not be read as 'key free'."""
    storage, state = fake_s3_storage(
        errors={"head_object": client_error("AccessDenied", 403)}
    )
    with pytest.raises(EvidenceStorageError):
        await storage.put(str(uuid.uuid4()), _chunks(b"forbidden head"), attempt_no=1)
    assert ops(state, "copy_object:dest") == []


async def test_s3_head_transient_never_copies():
    storage, state = fake_s3_storage(
        errors={"head_object": client_error("ServiceUnavailable", 503)}
    )
    with pytest.raises(EvidenceStorageError):
        await storage.put(str(uuid.uuid4()), _chunks(b"flaky head"), attempt_no=1)
    assert ops(state, "copy_object:dest") == []


async def test_s3_unrecognised_head_error_never_copies():
    """An error the adapter cannot classify is still an error — never 'absent'."""
    storage, state = fake_s3_storage(
        errors={"head_object": client_error("InvalidRequest", 400)}
    )
    with pytest.raises(EvidenceStorageError):
        await storage.put(str(uuid.uuid4()), _chunks(b"odd head"), attempt_no=1)
    assert ops(state, "copy_object:dest") == []


async def test_s3_exists_forbidden_raises_instead_of_false():
    storage, _ = fake_s3_storage(errors={"head_object": client_error("AccessDenied", 403)})
    with pytest.raises(EvidenceStorageError):
        await storage.exists("evidence/x/deadbeefdeadbeef")


async def test_s3_exists_transient_raises_instead_of_false():
    storage, _ = fake_s3_storage(errors={"head_object": client_error("SlowDown", 503)})
    with pytest.raises(EvidenceStorageError):
        await storage.exists("evidence/x/deadbeefdeadbeef")


async def test_s3_open_forbidden_is_not_object_not_found():
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"bytes", 1)
    storage, _ = fake_s3_storage(state, errors={"get_object": client_error("AccessDenied", 403)})
    with pytest.raises(EvidenceStorageError) as info:
        async for _ in storage.open("evidence/x/k"):
            pass
    assert not isinstance(info.value, ObjectNotFound)


async def test_s3_stream_read_error_is_a_storage_error():
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"a" * 10, 1)
    storage, _ = fake_s3_storage(
        state, stream_error=ReadTimeoutError(endpoint_url="http://fake"), stream_error_after=1
    )
    with pytest.raises(EvidenceStorageError):
        async for _ in storage.open("evidence/x/k"):
            pass


@pytest.mark.parametrize("operation", ["put", "open", "exists"])
async def test_s3_client_entry_failure_is_a_storage_error(operation):
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"bytes", 1)
    storage, _ = fake_s3_storage(state, enter_error=NoCredentialsError())
    with pytest.raises(EvidenceStorageError):
        if operation == "put":
            await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
        elif operation == "open":
            async for _ in storage.open("evidence/x/k"):
                pass
        else:
            await storage.exists("evidence/x/k")


# --------------------------------------------------- WP-S(4) R7 / R8 defects


@pytest.mark.parametrize("exc_type", [SiteLogTooLarge, EvidenceTooLarge])
async def test_s3_source_exception_keeps_its_type_and_aborts(exc_type):
    """R7: the size-cap exception from the chunk source must reach the
    service unchanged (413), with this attempt's multipart upload aborted."""
    storage, state = fake_s3_storage()
    eid = str(uuid.uuid4())

    async def capped():
        yield b"first"
        raise exc_type()

    with pytest.raises(exc_type):
        await storage.put(eid, capped(), attempt_no=2)
    staging = s3_staging_key(eid, 2)
    assert [k for k, _ in state.aborts] == [staging]
    assert ops(state, "copy_object:dest") == []
    assert ops(state, "delete_object") == []


async def test_local_source_exception_leaves_no_part_file(tmp_path):
    """R8: a non-OSError source exception propagates AND the .part is removed."""
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())

    async def capped():
        yield b"first"
        raise SiteLogTooLarge()

    with pytest.raises(SiteLogTooLarge):
        await storage.put(eid, capped(), attempt_no=1)
    assert not (tmp_path / ".staging" / local_staging_name(eid, 1)).exists()
    assert not (tmp_path / "evidence").exists()


async def test_local_cancellation_propagates_and_removes_part(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    started = asyncio.Event()

    async def stalled():
        yield b"first"
        started.set()
        await asyncio.Event().wait()  # never released: cancelled from outside

    task = asyncio.create_task(storage.put(eid, stalled(), attempt_no=1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / ".staging" / local_staging_name(eid, 1)).exists()
    assert not (tmp_path / "evidence").exists()


async def test_s3_cancellation_propagates_and_aborts_multipart():
    storage, state = fake_s3_storage()
    eid = str(uuid.uuid4())
    started = asyncio.Event()

    async def stalled():
        yield b"first"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(storage.put(eid, stalled(), attempt_no=3))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [k for k, _ in state.aborts] == [s3_staging_key(eid, 3)]
    assert ops(state, "copy_object:dest") == []


# --------------------------------------------------------- FD1 mechanism


@pytest.mark.parametrize("variant", ["identical_bytes", "shared_prefix_different_bytes"])
async def test_s3_stale_attempt_cannot_touch_the_newer_attempts_object(monkeypatch, variant):
    """Attempt 1 passes its HEAD, parks before its copy; attempt 2 runs to
    completion; attempt 1 resumes. The object attempt 2 confirmed must keep
    its bytes AND its identity (write version 1).

    ``identical_bytes`` reproduces the identity overwrite on attempt-free
    keys; ``shared_prefix_different_bytes`` pins the sha prefix (a simulated
    64-bit locator collision) and reproduces content damage.
    """
    payload_1 = b"attempt one bytes"
    payload_2 = payload_1 if variant == "identical_bytes" else b"attempt two bytes"
    if variant != "identical_bytes":
        original = es.make_object_key

        def collided(evidence_id, sha256_hex, *args, **kwargs):
            return original(evidence_id, "f" * 64, *args, **kwargs)

        monkeypatch.setattr(es, "make_object_key", collided)

    eid = str(uuid.uuid4())
    release = asyncio.Event()
    park = {("copy_object", s3_staging_key(eid, 1)): release}
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park=park)

    stale = asyncio.create_task(storage.put(eid, _chunks(payload_1), attempt_no=1))
    await until(lambda: ("copy_object", s3_staging_key(eid, 1)) in state.parked)

    newer = await storage.put(eid, _chunks(payload_2), attempt_no=2)
    confirmed = state.objects[newer.key]
    assert confirmed.data == payload_2 and confirmed.version == 1

    release.set()
    old = await stale
    # Damage assertions FIRST so the base-run log records the overwrite
    # (identity: version 2; content: payload_1 at the newer key), not
    # merely "same key".
    assert state.objects[newer.key].version == 1
    assert state.objects[newer.key].data == payload_2
    assert old.key != newer.key  # the mechanism: one key per attempt
    assert state.objects[old.key].data == payload_1
    assert hashlib.sha256(payload_2).hexdigest() == newer.sha256
    assert newer.key.startswith(f"evidence/{eid}/")
