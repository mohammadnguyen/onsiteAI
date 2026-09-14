"""Contract tests for the evidence storage abstraction.

The interface guarantees under test:

* put/open/exists roundtrip with verified size + sha256;
* immutable unique object key — legacy ``evidence/{id}/{sha[:16]}`` for
  ``attempt_no=None``, attempt-scoped ``…/{sha[:16]}.a{N}`` for numbered
  uploads (WP-S-core, FD1 2026-09-14; reverses the A1b "final key unchanged
  by attempt" ruling for the numbered path only);
* no overwrite — a second put of identical content to the same key fails
  and leaves the existing object untouched;
* key isolation — a stale attempt can publish only at its own key;
* chunked streaming — the adapter never receives (or needs) the whole
  payload at once, and readback yields bounded chunks;
* error classification (WP-S(1)) — absent / transient / permanent /
  unclassified, never a silent "absent";
* cleanup on failure (WP-S(4)) — own staging only; source exceptions and
  cancellation re-raised unchanged, backend errors classified; final objects
  never deleted;
* the interface exposes no delete (retention by construction).

Labels: tests marked ``# legitimate-behaviour`` assert behaviour that is
semantically unchanged from the base (F2). This module itself is NOT
base-importable (it names the new error classes); the base-runnable set is
test_evidence_storage_defects.py, and the labels were checked by running the
labelled tests with the four new names shimmed onto a base checkout.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import ssl
import uuid
from pathlib import Path

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
from botocore.exceptions import SSLError as BotoSSLError

from app.services.evidence import EvidenceTooLarge
from app.services.evidence_storage import (
    CHUNK_SIZE,
    EvidenceStorage,
    EvidenceStorageError,
    LocalEvidenceStorage,
    ObjectAlreadyExists,
    ObjectNotFound,
    S3EvidenceStorage,
    StoragePermanentError,
    StorageTransientError,
    attempt_suffix,
    classify_storage_exception,
    local_staging_name,
    make_object_key,
    s3_staging_key,
    staging_suffix,
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


async def _chunks(payload: bytes, size: int = CHUNK_SIZE):
    for i in range(0, len(payload), size):
        yield payload[i : i + size]


def _identity(path) -> tuple[int, int, int]:
    st = os.stat(path)
    return (st.st_ino, st.st_mtime_ns, st.st_size)


# ------------------------------------------------------------ local contract


async def test_put_open_exists_roundtrip(tmp_path):  # legitimate-behaviour
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"site voice memo bytes" * 1000
    evidence_id = str(uuid.uuid4())

    stored = await storage.put(evidence_id, _chunks(payload))

    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.key == make_object_key(evidence_id, stored.sha256)
    assert stored.key.startswith(f"evidence/{evidence_id}/")
    assert await storage.exists(stored.key)

    read_back = b""
    async for chunk in storage.open(stored.key):
        assert len(chunk) <= CHUNK_SIZE
        read_back += chunk
    assert read_back == payload


async def test_no_overwrite_same_content(tmp_path):  # legitimate-behaviour
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"immutable evidence"
    evidence_id = str(uuid.uuid4())

    await storage.put(evidence_id, _chunks(payload))
    with pytest.raises(ObjectAlreadyExists):
        await storage.put(evidence_id, _chunks(payload))


async def test_open_missing_key_raises(tmp_path):  # legitimate-behaviour
    storage = LocalEvidenceStorage(tmp_path)
    with pytest.raises(ObjectNotFound):
        storage.open("evidence/nope/deadbeef")


async def test_streaming_is_chunked_multi_chunk_payload(tmp_path):  # legitimate-behaviour
    """A payload spanning many chunks arrives as bounded pieces.

    Guards the 512MB-VM constraint at the adapter contract level: the
    writer receives an async iterator and consumes it piecewise; at no
    point does the contract require the full payload as one object.
    """
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"x" * (3 * CHUNK_SIZE + 17)
    seen_sizes: list[int] = []

    async def spying_chunks():
        async for chunk in _chunks(payload):
            seen_sizes.append(len(chunk))
            yield chunk

    stored = await storage.put(str(uuid.uuid4()), spying_chunks())

    assert stored.size_bytes == len(payload)
    assert len(seen_sizes) == 4
    assert all(size <= CHUNK_SIZE for size in seen_sizes)


async def test_source_failure_keeps_type_and_leaves_no_object_or_staging(tmp_path):
    """WP-S(4): an exception raised by the chunk SOURCE propagates as itself
    (here an OSError from the request side) and leaves nothing behind."""
    storage = LocalEvidenceStorage(tmp_path)

    async def exploding_chunks():
        yield b"partial"
        raise OSError("client side gone")

    with pytest.raises(OSError) as info:
        await storage.put(str(uuid.uuid4()), exploding_chunks())
    assert not isinstance(info.value, EvidenceStorageError)

    staging = tmp_path / ".staging"
    assert not any(staging.glob("*")) if staging.exists() else True
    assert not (tmp_path / "evidence").exists()


async def test_adapter_io_failure_is_classified_and_leaves_no_staging(tmp_path, monkeypatch):
    """The adapter's OWN filesystem failure is a classified storage error."""
    storage = LocalEvidenceStorage(tmp_path)

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(StorageTransientError):
        await storage.put(str(uuid.uuid4()), _chunks(b"bytes"), attempt_no=1)
    assert not any((tmp_path / ".staging").glob("*"))
    assert not any((tmp_path / "evidence").rglob("*.a1"))


async def test_local_permission_denied_is_permanent(tmp_path, monkeypatch):
    storage = LocalEvidenceStorage(tmp_path)

    def denied(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(StoragePermanentError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"bytes"), attempt_no=1)
    assert info.value.failure_class == "permanent"
    assert not any((tmp_path / ".staging").glob("*"))


async def test_interface_has_no_delete():  # legitimate-behaviour
    """Retention by construction: the storage contract cannot delete.

    DEC-EVIDENCE-001 — this is the deterministic guarantee that no
    normal product path (or future extraction pipeline holding this
    interface) can destroy raw evidence. A privileged purge would be a
    separate promoted decision with its own restricted interface.
    """
    forbidden = [
        name
        for name in ("delete", "remove", "purge", "unlink", "overwrite")
        if hasattr(EvidenceStorage, name)
        or hasattr(LocalEvidenceStorage, name)
        or hasattr(S3EvidenceStorage, name)
    ]
    assert forbidden == []


# ---------------------------------------------------------------------------
# Attempt discriminator: staging (A1b) and final keys (WP-S-core / FD1).
# ---------------------------------------------------------------------------


async def test_attempt_suffix_validation():
    assert attempt_suffix(None) == ""
    assert attempt_suffix(1) == ".a1"
    assert attempt_suffix(42) == ".a42"
    assert staging_suffix is attempt_suffix  # A1b name kept
    for bad in (0, -1, True, False, "1", 1.5):
        with pytest.raises(ValueError):
            attempt_suffix(bad)


async def test_legacy_staging_paths_are_byte_identical_for_both_backends():  # legitimate-behaviour
    eid = str(uuid.uuid4())
    assert local_staging_name(eid, None) == f"{eid}.part"
    assert s3_staging_key(eid, None) == f"evidence/{eid}/.staging"


async def test_attempt_staging_isolated_per_attempt_both_backends():  # legitimate-behaviour
    eid = str(uuid.uuid4())
    local = {local_staging_name(eid, n) for n in (1, 2, 3)}
    local.add(local_staging_name(eid, None))
    assert len(local) == 4  # legacy + three attempts, all distinct
    s3 = {s3_staging_key(eid, n) for n in (1, 2, 3)}
    s3.add(s3_staging_key(eid, None))
    assert len(s3) == 4


async def test_make_object_key_legacy_and_attempt_forms():
    """FD1 acceptance: legacy key byte-identical; numbered key = legacy + .aN."""
    eid = str(uuid.uuid4())
    sha = hashlib.sha256(b"payload").hexdigest()
    legacy = f"evidence/{eid}/{sha[:16]}"
    assert make_object_key(eid, sha) == legacy
    assert make_object_key(eid, sha, None) == legacy
    assert make_object_key(eid, sha, 1) == legacy + ".a1"
    assert make_object_key(eid, sha, 12) == legacy + ".a12"
    assert make_object_key(eid, sha, 1) != make_object_key(eid, sha, 2)
    for bad in (0, -3, True, "2", 2.0):
        with pytest.raises(ValueError):
            make_object_key(eid, sha, bad)


async def test_legacy_put_final_key_byte_identical(tmp_path):  # legitimate-behaviour
    """Callers that pass no attempt (the /evidence upload path) keep the exact
    pre-WP-S-core key, so existing rows read their objects unchanged."""
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"legacy upload path"
    eid = str(uuid.uuid4())
    stored = await storage.put(eid, _chunks(payload))
    sha = hashlib.sha256(payload).hexdigest()
    assert stored.key == f"evidence/{eid}/{sha[:16]}"
    assert ".a" not in stored.key
    assert (tmp_path / stored.key).read_bytes() == payload


async def test_final_key_isolated_per_attempt_identical_bytes(tmp_path):
    """FD1 acceptance: identical bytes under attempts 1 and 2 produce two
    distinct objects; attempt 1's object keeps bytes and identity."""
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"identical bytes across attempts"
    eid = str(uuid.uuid4())
    sha = hashlib.sha256(payload).hexdigest()

    first = await storage.put(eid, _chunks(payload), attempt_no=1)
    first_identity = _identity(tmp_path / first.key)
    second = await storage.put(eid, _chunks(payload), attempt_no=2)

    assert first.key == make_object_key(eid, sha, 1)
    assert second.key == make_object_key(eid, sha, 2)
    assert first.key != second.key
    assert first.sha256 == second.sha256 == sha
    assert (tmp_path / first.key).read_bytes() == payload
    assert (tmp_path / second.key).read_bytes() == payload
    assert _identity(tmp_path / first.key) == first_identity
    assert await storage.exists(first.key) and await storage.exists(second.key)
    # The legacy key for the same bytes is untouched — nothing was written there.
    assert not await storage.exists(make_object_key(eid, sha))


async def test_same_attempt_sequential_rewrite_rejected_object_unchanged(tmp_path):
    """A second put for the SAME (evidence, attempt) and bytes is rejected;
    the existing object keeps bytes and identity; no staging residue."""
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"same attempt twice"
    eid = str(uuid.uuid4())

    stored = await storage.put(eid, _chunks(payload), attempt_no=1)
    identity = _identity(tmp_path / stored.key)
    with pytest.raises(ObjectAlreadyExists) as info:
        await storage.put(eid, _chunks(payload), attempt_no=1)

    assert str(info.value) == stored.key
    assert stored.key.endswith(".a1")
    assert (tmp_path / stored.key).read_bytes() == payload
    assert _identity(tmp_path / stored.key) == identity
    assert not any((tmp_path / ".staging").glob("*"))


async def test_local_stale_attempt_publishes_only_at_its_own_key(tmp_path):
    """Key isolation on the local adapter: attempt 1 parks mid-stream,
    attempt 2 confirms, attempt 1 resumes — attempt 2's object is untouched.

    Claim scope: isolation ACROSS attempts by key construction. Two
    concurrent puts for the SAME attempt never happen through the Site Log
    service (a pending row answers SiteLogUploadInProgress under the
    manifest-row lock; the attempt number only increments) - the adapter
    itself does not make them atomic.
    """
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"stale writer bytes"
    eid = str(uuid.uuid4())
    parked = asyncio.Event()
    release = asyncio.Event()

    async def parking_chunks():
        yield payload[:5]
        parked.set()
        await release.wait()
        yield payload[5:]

    stale = asyncio.create_task(storage.put(eid, parking_chunks(), attempt_no=1))
    await parked.wait()
    newer = await storage.put(eid, _chunks(payload), attempt_no=2)
    newer_identity = _identity(tmp_path / newer.key)

    release.set()
    old = await stale
    assert old.key != newer.key
    assert old.key.endswith(".a1") and newer.key.endswith(".a2")
    assert (tmp_path / newer.key).read_bytes() == payload
    assert _identity(tmp_path / newer.key) == newer_identity
    assert (tmp_path / old.key).read_bytes() == payload


async def test_attempt_staging_observed_and_isolated_mid_stream(tmp_path):  # legitimate-behaviour
    """While attempt 2 streams, its staging file is the attempt-scoped
    name — and a pre-existing attempt-1 staging file is untouched."""
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    other = staging_dir / local_staging_name(eid, 1)
    other.write_bytes(b"attempt-1 in-flight bytes")

    seen: list[str] = []

    async def observing_chunks():
        yield b"first"
        seen.extend(sorted(p.name for p in staging_dir.iterdir()))
        yield b"second"

    await storage.put(eid, observing_chunks(), attempt_no=2)
    assert local_staging_name(eid, 2) in seen
    assert other.read_bytes() == b"attempt-1 in-flight bytes"


async def test_failed_attempt_cleanup_cannot_affect_other_attempt(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    survivor = staging_dir / local_staging_name(eid, 2)
    survivor.write_bytes(b"newer attempt in flight")

    async def exploding():
        yield b"partial"
        raise SiteLogTooLarge()

    with pytest.raises(SiteLogTooLarge):
        await storage.put(eid, exploding(), attempt_no=1)

    assert not (staging_dir / local_staging_name(eid, 1)).exists()
    assert survivor.read_bytes() == b"newer attempt in flight"
    assert not (tmp_path / "evidence").exists()


async def test_invalid_attempt_rejected_before_any_mutation(tmp_path):  # legitimate-behaviour
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await storage.put(eid, _chunks(b"payload"), attempt_no=0)
    assert not (tmp_path / ".staging").exists()
    assert not (tmp_path / "evidence").exists()


async def test_s3_invalid_attempt_rejected_before_any_client_use():  # legitimate-behaviour
    """Validation precedes the client context: no network is attempted."""
    storage = S3EvidenceStorage(
        endpoint_url="http://storage.invalid.localdomain:1", bucket="never"
    )
    with pytest.raises(ValueError):
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=-1)


async def test_no_payload_content_in_logs(tmp_path, caplog):  # legitimate-behaviour
    storage = LocalEvidenceStorage(tmp_path)
    marker = b"SECRET-CAPTURE-CONTENT-MARKER"

    async def exploding():
        yield marker
        raise EvidenceTooLarge()

    with caplog.at_level("DEBUG"):
        with pytest.raises(EvidenceTooLarge):
            await storage.put(str(uuid.uuid4()), exploding(), attempt_no=1)
        await storage.put(str(uuid.uuid4()), _chunks(marker), attempt_no=1)
    assert marker.decode() not in caplog.text


# ---------------------------------------------------------------------------
# S3 adapter wiring against the recording fake (no network).
# ---------------------------------------------------------------------------


async def test_s3_put_wires_attempt_scoped_staging_and_final_keys():
    storage, state = fake_s3_storage()
    payload = b"s3 attempt wiring"
    eid = str(uuid.uuid4())
    sha = hashlib.sha256(payload).hexdigest()

    stored = await storage.put(eid, _chunks(payload), attempt_no=2)

    expected_staging = f"evidence/{eid}/.staging.a2"
    legacy_staging = f"evidence/{eid}/.staging"
    expected_final = make_object_key(eid, sha, 2)
    legacy_final = make_object_key(eid, sha)

    staging_ops = {
        "create_multipart_upload",
        "upload_part",
        "complete_multipart_upload",
        "copy_object:source",
        "delete_object",
    }
    for op, key in state.calls:
        if op in staging_ops:
            assert key == expected_staging, (op, key)
    assert all(key != legacy_staging for _, key in state.calls)
    # FD1: the final key is attempt-scoped; the legacy key is never touched.
    assert stored.key == expected_final == legacy_final + ".a2"
    assert ("copy_object:dest", expected_final) in state.calls
    assert ("head_object", expected_final) in state.calls
    assert all(key != legacy_final for _, key in state.calls)
    assert state.objects[expected_final].data == payload
    assert legacy_final not in state.objects
    assert ops(state, "delete_object") == [expected_staging]


async def test_s3_put_legacy_path_uses_exact_legacy_keys():  # legitimate-behaviour
    storage, state = fake_s3_storage()
    payload = b"s3 legacy wiring"
    eid = str(uuid.uuid4())

    stored = await storage.put(eid, _chunks(payload))

    legacy_staging = f"evidence/{eid}/.staging"
    for op, key in state.calls:
        if op in {
            "create_multipart_upload",
            "upload_part",
            "complete_multipart_upload",
            "copy_object:source",
            "delete_object",
        }:
            assert key == legacy_staging, (op, key)
    assert ".a" not in stored.key
    assert stored.key == make_object_key(eid, hashlib.sha256(payload).hexdigest())
    assert state.objects[stored.key].data == payload


async def test_s3_same_attempt_sequential_rewrite_rejected_object_unchanged():
    state = FakeS3State()
    storage, _ = fake_s3_storage(state)
    payload = b"same attempt twice on s3"
    eid = str(uuid.uuid4())

    stored = await storage.put(eid, _chunks(payload), attempt_no=1)
    before = state.objects[stored.key]
    with pytest.raises(ObjectAlreadyExists) as info:
        await storage.put(eid, _chunks(payload), attempt_no=1)

    assert str(info.value) == stored.key
    assert stored.key.endswith(".a1")
    assert state.objects[stored.key] is before  # never rewritten
    assert before.version == 1 and before.data == payload
    assert ops(state, "copy_object:dest") == [stored.key]  # exactly one copy ever
    # the rejected attempt deleted only its own (completed) staging object
    assert ops(state, "delete_object") == [s3_staging_key(eid, 1)] * 2
    assert s3_staging_key(eid, 1) not in state.objects


async def test_s3_identical_bytes_across_attempts_are_distinct_objects():
    state = FakeS3State()
    storage, _ = fake_s3_storage(state)
    payload = b"identical bytes, two attempts"
    eid = str(uuid.uuid4())
    sha = hashlib.sha256(payload).hexdigest()

    first = await storage.put(eid, _chunks(payload), attempt_no=1)
    second = await storage.put(eid, _chunks(payload), attempt_no=2)

    assert first.key == make_object_key(eid, sha, 1)
    assert second.key == make_object_key(eid, sha, 2)
    assert state.objects[first.key].version == 1
    assert state.objects[second.key].version == 1
    assert state.objects[first.key].data == state.objects[second.key].data == payload


async def test_s3_stale_attempt_publishes_only_at_its_own_key():
    """Attempt 1 passes HEAD and parks before its copy; attempt 2 confirms;
    attempt 1 resumes and lands at .a1 — attempt 2's object keeps bytes and
    write version (identity). The mechanism is the key, not a lock."""
    eid = str(uuid.uuid4())
    payload = b"stale s3 writer"
    release = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("copy_object", s3_staging_key(eid, 1)): release})

    stale = asyncio.create_task(storage.put(eid, _chunks(payload), attempt_no=1))
    await until(lambda: ("copy_object", s3_staging_key(eid, 1)) in state.parked)
    newer = await storage.put(eid, _chunks(payload), attempt_no=2)
    assert state.objects[newer.key].version == 1

    release.set()
    old = await stale
    assert old.key != newer.key
    assert state.objects[newer.key].version == 1
    assert state.objects[newer.key].data == payload
    assert state.objects[old.key].data == payload
    assert ops(state, "copy_object:dest") == [newer.key, old.key]


async def test_s3_failure_touches_only_current_attempt_staging_key():  # legitimate-behaviour
    """A failing attempt-3 upload never touches attempt-2's staging key,
    the legacy key, or any final key."""
    storage, state = fake_s3_storage(fail_on="complete_multipart_upload")
    eid = str(uuid.uuid4())

    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(b"doomed bytes"), attempt_no=3)

    own_staging = f"evidence/{eid}/.staging.a3"
    assert state.calls, "fake client saw no calls"
    for op, key in state.calls:
        if op != "client_exit":
            assert key == own_staging, key
    assert all(k != f"evidence/{eid}/.staging" for _, k in state.calls)
    assert all(k != f"evidence/{eid}/.staging.a2" for _, k in state.calls)


# ---------------------------------------------------------------------------
# WP A A2a — S3 staging cleanup contract (Revision 2.1 §5), now with the
# WP-S(4) additions: source exceptions and cancellation.
# ---------------------------------------------------------------------------


async def test_s3_pre_completion_failure_aborts_exact_attempt():
    storage, state = fake_s3_storage(fail_on="upload_part")
    eid = str(uuid.uuid4())
    with pytest.raises(EvidenceStorageError) as info:
        await storage.put(eid, _chunks(b"doomed"), attempt_no=4)
    assert "injected failure in upload_part" in str(info.value)
    assert info.value.failure_class is None  # RuntimeError: unclassified, still raised
    staging = f"evidence/{eid}/.staging.a4"
    assert state.aborts == [(staging, "fake-upload-id-1")]
    assert ops(state, "delete_object") == []
    assert all(k == staging for op, k in state.calls if op != "client_exit")


async def test_s3_post_completion_failure_deletes_staging_not_final():
    storage, state = fake_s3_storage(fail_on="copy_object:dest")
    eid = str(uuid.uuid4())
    payload = b"copy fails"
    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(payload), attempt_no=1)
    staging = f"evidence/{eid}/.staging.a1"
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest(), 1)
    assert state.aborts == []
    assert ops(state, "delete_object") == [staging]
    assert final not in ops(state, "delete_object")


async def test_s3_collision_deletes_own_staging_never_final():
    storage, state = fake_s3_storage(final_exists=True)
    eid = str(uuid.uuid4())
    payload = b"identical bytes"
    with pytest.raises(ObjectAlreadyExists) as info:
        await storage.put(eid, _chunks(payload), attempt_no=2)
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest(), 2)
    assert str(info.value) == final
    assert state.aborts == []
    assert ops(state, "delete_object") == [f"evidence/{eid}/.staging.a2"]
    assert ops(state, "copy_object:dest") == []  # no second copy over final


async def test_s3_cleanup_failure_never_masks_primary_error(caplog):  # legitimate-behaviour
    storage, _ = fake_s3_storage(fail_on="upload_part", cleanup_fail=True)
    with caplog.at_level("WARNING"), pytest.raises(EvidenceStorageError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
    assert "injected failure in upload_part" in str(info.value)
    assert "cleanup" not in str(info.value)
    assert any("staging cleanup failed" in r.getMessage() for r in caplog.records)
    # post-completion variant
    storage2, _ = fake_s3_storage(fail_on="copy_object:dest", cleanup_fail=True)
    with pytest.raises(EvidenceStorageError) as info2:
        await storage2.put(str(uuid.uuid4()), _chunks(b"y"), attempt_no=1)
    assert "injected failure in copy_object:dest" in str(info2.value)


async def test_s3_cleanup_failure_never_masks_source_exception(caplog):
    storage, state = fake_s3_storage(cleanup_fail=True)

    async def capped():
        yield b"first"
        raise EvidenceTooLarge()

    with caplog.at_level("WARNING"), pytest.raises(EvidenceTooLarge):
        await storage.put(str(uuid.uuid4()), capped(), attempt_no=1)
    assert len(state.aborts) == 1
    assert any("staging cleanup failed" in r.getMessage() for r in caplog.records)


async def test_s3_delete_failure_after_valid_final_still_succeeds(caplog):
    storage, state = fake_s3_storage(cleanup_fail=True)
    eid = str(uuid.uuid4())
    payload = b"final is valid"
    with caplog.at_level("WARNING"):
        stored = await storage.put(eid, _chunks(payload), attempt_no=3)
    assert stored.key == make_object_key(eid, hashlib.sha256(payload).hexdigest(), 3)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert ops(state, "delete_object") == [f"evidence/{eid}/.staging.a3"]
    assert any("cleanup failed after valid final object" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "fail_on, errors, final_exists, source_exc",
    [
        (None, None, False, None),
        ("upload_part", None, False, None),
        ("complete_multipart_upload", None, False, None),
        ("copy_object:dest", None, False, None),
        (None, None, True, None),
        ("create_multipart_upload", None, False, None),
        (None, {"head_object": client_error("AccessDenied", 403)}, False, None),
        (None, {"head_object": client_error("ServiceUnavailable", 503)}, False, None),
        (None, None, False, SiteLogTooLarge),
        (None, None, False, EvidenceTooLarge),
    ],
)
async def test_s3_delete_never_receives_final_key(fail_on, errors, final_exists, source_exc):
    storage, state = fake_s3_storage(fail_on=fail_on, errors=errors, final_exists=final_exists)
    eid = str(uuid.uuid4())
    payload = b"never delete final"
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest(), 1)

    async def source():
        yield payload
        if source_exc is not None:
            raise source_exc()

    with contextlib.suppress(EvidenceStorageError, SiteLogTooLarge, EvidenceTooLarge):
        await storage.put(eid, source(), attempt_no=1)
    for key in ops(state, "delete_object") + [k for k, _ in state.aborts]:
        assert key == f"evidence/{eid}/.staging.a1"
        assert key != final
    if fail_on == "create_multipart_upload":
        assert state.aborts == [] and ops(state, "delete_object") == []


async def test_s3_legacy_path_abort_uses_legacy_key():  # legitimate-behaviour
    storage, state = fake_s3_storage(fail_on="upload_part")
    eid = str(uuid.uuid4())
    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(b"legacy"))
    assert state.aborts == [(f"evidence/{eid}/.staging", "fake-upload-id-1")]


async def test_s3_cancellation_propagates_and_aborts_only_own_upload():
    state = FakeS3State()
    storage, _ = fake_s3_storage(state)
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
    assert state.aborts == [(s3_staging_key(eid, 3), "fake-upload-id-1")]
    assert ops(state, "delete_object") == []
    assert ops(state, "copy_object:dest") == []
    assert state.uploads == {}


async def test_s3_source_storage_error_passes_through_unchanged():
    """A storage error raised by the source (e.g. a wrapping storage) is the
    same object on the way out — no re-wrapping, own upload aborted."""
    storage, state = fake_s3_storage()
    original = StorageTransientError("upstream adapter said so")

    async def capped():
        yield b"first"
        raise original

    with pytest.raises(StorageTransientError) as info:
        await storage.put(str(uuid.uuid4()), capped(), attempt_no=1)
    assert info.value is original
    assert len(state.aborts) == 1


# ---------------------------------------------------------------------------
# WP-S(1) — error classification acceptance.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, expected",
    [
        (client_error("404", 404, "HeadObject"), "absent"),
        (client_error("NoSuchKey", 404, "GetObject"), "absent"),
        (client_error("NotFound", 404, "HeadObject"), "absent"),
        (client_error("AccessDenied", 403), "permanent"),
        (client_error("403", 403), "permanent"),
        (client_error("InvalidAccessKeyId", 403), "permanent"),
        (client_error("SignatureDoesNotMatch", 403), "permanent"),
        (client_error("NoSuchBucket", 404), "permanent"),  # code beats the 404 status
        (client_error("PermanentRedirect", 301), "permanent"),
        (client_error("ServiceUnavailable", 503), "transient"),
        (client_error("SlowDown", 503), "transient"),
        (client_error("InternalError", 500), "transient"),
        (client_error("RequestTimeout", 400), "transient"),
        (client_error("Throttling", 400), "transient"),
        (client_error("InvalidRequest", 400), "unclassified"),
        (client_error("EntityTooSmall", 400), "unclassified"),
        (EndpointConnectionError(endpoint_url="http://fake"), "transient"),
        (ReadTimeoutError(endpoint_url="http://fake"), "transient"),
        (NoCredentialsError(), "permanent"),
        (ConnectionResetError(), "transient"),
        (TimeoutError(), "transient"),
        (RuntimeError("injected"), "unclassified"),
        (KeyError("404: no such key"), "unclassified"),
        # TLS: a certificate failure wrapped by botocore is configuration
        # (permanent); a bare SSL transport error stays transient.
        (
            BotoSSLError(
                endpoint_url="https://fake",
                error=ssl.SSLCertVerificationError(1, "certificate verify failed"),
            ),
            "permanent",
        ),
        (BotoSSLError(endpoint_url="https://fake", error=OSError("EOF")), "transient"),
        (ssl.SSLCertVerificationError(1, "certificate verify failed"), "permanent"),
        (PermissionError(13, "Permission denied"), "permanent"),
        (FileNotFoundError(2, "no such CA bundle"), "permanent"),
        (ValueError("Invalid endpoint: x"), "unclassified"),
    ],
)
async def test_classification_table(exc, expected):
    assert classify_storage_exception(exc) == expected


async def test_s3_head_forbidden_is_permanent_and_never_copies():
    storage, state = fake_s3_storage(errors={"head_object": client_error("AccessDenied", 403)})
    eid = str(uuid.uuid4())
    with pytest.raises(StoragePermanentError) as info:
        await storage.put(eid, _chunks(b"forbidden"), attempt_no=1)
    assert info.value.failure_class == "permanent"
    assert ops(state, "copy_object:dest") == []
    # post-completion failure: the completed staging object is deleted, never a final key
    assert ops(state, "delete_object") == [s3_staging_key(eid, 1)]


async def test_s3_head_transient_is_transient_and_never_copies():
    storage, state = fake_s3_storage(errors={"head_object": client_error("SlowDown", 503)})
    with pytest.raises(StorageTransientError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"slow"), attempt_no=1)
    assert info.value.failure_class == "transient"
    assert ops(state, "copy_object:dest") == []


async def test_s3_head_unclassified_is_raised_and_never_copies():
    storage, state = fake_s3_storage(errors={"head_object": client_error("InvalidRequest", 400)})
    with pytest.raises(EvidenceStorageError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"odd"), attempt_no=1)
    assert type(info.value) is EvidenceStorageError
    assert info.value.failure_class is None
    assert ops(state, "copy_object:dest") == []


async def test_s3_exists_classes():
    state = FakeS3State()
    state.objects["evidence/x/present"] = FakeObject(b"b", 1)
    storage, _ = fake_s3_storage(state)
    assert await storage.exists("evidence/x/present") is True
    assert await storage.exists("evidence/x/absent") is False

    forbidden, _ = fake_s3_storage(state, errors={"head_object": client_error("AccessDenied", 403)})
    with pytest.raises(StoragePermanentError):
        await forbidden.exists("evidence/x/present")
    flaky, _ = fake_s3_storage(state, errors={"head_object": client_error("503", 503)})
    with pytest.raises(StorageTransientError):
        await flaky.exists("evidence/x/present")
    odd, _ = fake_s3_storage(state, errors={"head_object": RuntimeError("weird")})
    with pytest.raises(EvidenceStorageError) as info:
        await odd.exists("evidence/x/present")
    assert info.value.failure_class is None


async def test_s3_open_classes():
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"payload bytes", 1)
    storage, _ = fake_s3_storage(state)
    read = b"".join([c async for c in storage.open("evidence/x/k")])
    assert read == b"payload bytes"

    with pytest.raises(ObjectNotFound):
        async for _ in storage.open("evidence/x/missing"):
            pass

    forbidden, _ = fake_s3_storage(state, errors={"get_object": client_error("AccessDenied", 403)})
    with pytest.raises(StoragePermanentError):
        async for _ in forbidden.open("evidence/x/k"):
            pass

    flaky, _ = fake_s3_storage(state, errors={"get_object": client_error("InternalError", 500)})
    with pytest.raises(StorageTransientError):
        async for _ in flaky.open("evidence/x/k"):
            pass


async def test_s3_stream_read_errors_are_classified():
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"a" * (2 * CHUNK_SIZE), 1)
    flaky, _ = fake_s3_storage(
        state, stream_error=ReadTimeoutError(endpoint_url="http://fake"), stream_error_after=1
    )
    got: list[bytes] = []
    with pytest.raises(StorageTransientError):
        async for chunk in flaky.open("evidence/x/k"):
            got.append(chunk)
    assert len(got) == 1  # first chunk delivered, then the classified failure

    odd, _ = fake_s3_storage(state, stream_error=RuntimeError("weird stream"), stream_error_after=0)
    with pytest.raises(EvidenceStorageError) as info:
        async for _ in odd.open("evidence/x/k"):
            pass
    assert info.value.failure_class is None


@pytest.mark.parametrize("operation", ["put", "open", "exists"])
async def test_s3_client_entry_failures_are_classified(operation):
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"bytes", 1)

    async def run(storage):
        if operation == "put":
            await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
        elif operation == "open":
            async for _ in storage.open("evidence/x/k"):
                pass
        else:
            await storage.exists("evidence/x/k")

    no_creds, _ = fake_s3_storage(state, enter_error=NoCredentialsError())
    with pytest.raises(StoragePermanentError):
        await run(no_creds)
    unreachable, _ = fake_s3_storage(
        state, enter_error=EndpointConnectionError(endpoint_url="http://fake")
    )
    with pytest.raises(StorageTransientError):
        await run(unreachable)
    assert state.calls == []  # nothing reached the client surface


async def test_classified_errors_stay_evidence_storage_errors():
    """Caller compatibility: every new class is an EvidenceStorageError and
    a botocore ClientError never escapes the adapter raw."""
    assert issubclass(StorageTransientError, EvidenceStorageError)
    assert issubclass(StoragePermanentError, EvidenceStorageError)
    assert issubclass(ObjectNotFound, EvidenceStorageError)
    assert issubclass(ObjectAlreadyExists, EvidenceStorageError)
    storage, _ = fake_s3_storage(errors={"upload_part": client_error("AccessDenied", 403)})
    with pytest.raises(EvidenceStorageError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
    assert not isinstance(info.value, ClientError)
    assert isinstance(info.value.__cause__, ClientError)
    assert info.value.failure_class == "permanent"


async def test_s3_client_construction_error_is_permanent():
    """The failure the SDK really raises at client entry (an invalid
    endpoint -> ValueError) is misconfiguration: permanent, never raw."""
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"bytes", 1)
    storage, _ = fake_s3_storage(state, enter_error=ValueError("Invalid endpoint: x"))
    with pytest.raises(StoragePermanentError):
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
    with pytest.raises(StoragePermanentError):
        async for _ in storage.open("evidence/x/k"):
            pass
    with pytest.raises(StoragePermanentError):
        await storage.exists("evidence/x/k")
    assert state.calls == []


async def test_s3_lazy_credential_errors_are_permanent_on_the_request_path():
    """botocore raises NoCredentialsError on the first signed request, not
    at client entry: the per-operation handlers classify it permanent."""
    state = FakeS3State()
    state.objects["evidence/x/k"] = FakeObject(b"bytes", 1)
    probe, _ = fake_s3_storage(state, errors={"head_object": NoCredentialsError()})
    with pytest.raises(StoragePermanentError):
        await probe.exists("evidence/x/k")
    writer, _ = fake_s3_storage(state, errors={"create_multipart_upload": NoCredentialsError()})
    with pytest.raises(StoragePermanentError):
        await writer.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
    assert state.aborts == []  # no upload was ever created


async def test_s3_cancellation_after_completion_deletes_only_own_staging():
    """Cancellation parked on the pre-copy HEAD (after the multipart upload
    completed) -> the completed staging object is deleted, no copy, no abort."""
    eid = str(uuid.uuid4())
    payload = b"cancelled after completion"
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest(), 2)
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("head_object", final): asyncio.Event()})

    task = asyncio.create_task(storage.put(eid, _chunks(payload), attempt_no=2))
    await until(lambda: ("head_object", final) in state.parked)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    staging = s3_staging_key(eid, 2)
    assert ops(state, "delete_object") == [staging]
    assert staging not in state.objects
    assert ops(state, "copy_object:dest") == []
    assert state.aborts == []
    assert final not in state.objects


async def test_local_probe_os_errors_are_classified(tmp_path, monkeypatch):
    storage = LocalEvidenceStorage(tmp_path)

    def denied(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", denied)
    with pytest.raises(StoragePermanentError):
        await storage.exists("evidence/x/deadbeefdeadbeef")
    with pytest.raises(StoragePermanentError):
        storage.open("evidence/x/deadbeefdeadbeef")


async def test_local_secondary_failure_while_unwinding_keeps_source_exception(
    tmp_path, monkeypatch, caplog
):
    """Source raises the size cap; closing the staging file then fails
    (disk full): the cap exception is what propagates, the .part is gone,
    the secondary failure is logged content-free."""
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    real_open = Path.open

    class _ExplodingClose:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            return self._fh.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            raise OSError(28, "No space left on device")

    def patched_open(self, mode="r", *args, **kwargs):
        fh = real_open(self, mode, *args, **kwargs)
        return _ExplodingClose(fh) if "w" in mode else fh

    monkeypatch.setattr(Path, "open", patched_open)

    async def capped():
        yield b"first"
        raise SiteLogTooLarge()

    with caplog.at_level("WARNING"), pytest.raises(SiteLogTooLarge):
        await storage.put(eid, capped(), attempt_no=1)
    assert not (tmp_path / ".staging" / local_staging_name(eid, 1)).exists()
    assert any("secondary failure" in r.getMessage() for r in caplog.records)
    assert "first" not in caplog.text


async def test_s3_cancellation_during_cleanup_lets_the_abort_finish():
    """Cancellation arriving while the abort is in flight: the abort still
    completes (shielded, bounded grace), then the cancellation propagates
    with the source failure kept as context."""
    eid = str(uuid.uuid4())
    staging = s3_staging_key(eid, 1)
    release = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("abort_multipart_upload", staging): release})

    async def capped():
        yield b"first"
        raise EvidenceTooLarge()

    observed: dict[str, BaseException | None] = {}

    async def run():
        try:
            await storage.put(eid, capped(), attempt_no=1)
        except asyncio.CancelledError as exc:
            observed["context"] = exc.__context__  # awaiting the task re-raises a fresh one
            raise

    task = asyncio.create_task(run())
    await until(lambda: ("abort_multipart_upload", staging) in state.parked)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert isinstance(observed["context"], EvidenceTooLarge)
    assert state.aborts == [(staging, "fake-upload-id-1")]
    assert state.uploads == {}  # the multipart upload was really aborted
    assert ops(state, "copy_object:dest") == []


async def test_s3_cancellation_during_post_completion_delete_lets_it_finish():
    eid = str(uuid.uuid4())
    staging = s3_staging_key(eid, 2)
    release = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(
        state,
        errors={"head_object": client_error("ServiceUnavailable", 503)},
        park={("delete_object", staging): release},
    )
    task = asyncio.create_task(storage.put(eid, _chunks(b"bytes"), attempt_no=2))
    await until(lambda: ("delete_object", staging) in state.parked)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ops(state, "delete_object") == [staging]
    assert staging not in state.objects
    assert state.aborts == [] and ops(state, "copy_object:dest") == []


async def test_s3_cleanup_outliving_the_grace_period_still_propagates_cancellation(
    monkeypatch, caplog
):
    import app.services.evidence_storage as es

    monkeypatch.setattr(es, "CLEANUP_GRACE_SECONDS", 0.05)
    eid = str(uuid.uuid4())
    staging = s3_staging_key(eid, 1)
    never = asyncio.Event()  # the abort never completes
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("abort_multipart_upload", staging): never})

    async def capped():
        yield b"first"
        raise SiteLogTooLarge()

    task = asyncio.create_task(storage.put(eid, capped(), attempt_no=1))
    await until(lambda: ("abort_multipart_upload", staging) in state.parked)
    task.cancel()
    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await task
    messages = [r.getMessage() for r in caplog.records]
    assert any("still in flight when cancellation propagated" in m for m in messages)
    assert ("abort_multipart_upload", staging) in state.parked  # it was really in flight
    never.set()  # let the parked fake finish so no task is left pending
    await asyncio.sleep(0)


async def test_s3_cancellation_at_source_with_stalled_cleanup_is_bounded(monkeypatch, caplog):
    """The cancellation itself interrupted the upload (primary is the
    CancelledError): a stalled abort still gets only the grace period."""
    import app.services.evidence_storage as es

    monkeypatch.setattr(es, "CLEANUP_GRACE_SECONDS", 0.05)
    eid = str(uuid.uuid4())
    staging = s3_staging_key(eid, 1)
    never = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("abort_multipart_upload", staging): never})
    started = asyncio.Event()

    async def stalled():
        yield b"first"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(storage.put(eid, stalled(), attempt_no=1))
    await started.wait()
    task.cancel()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await task
    assert loop.time() - t0 < 2.0  # bounded by the grace period, not by the stall
    assert ("abort_multipart_upload", staging) in state.parked
    messages = [r.getMessage() for r in caplog.records]
    assert any("still in flight when cancellation propagated" in m for m in messages)
    never.set()
    await asyncio.sleep(0)


async def test_s3_post_copy_cancellation_starts_no_second_cleanup(monkeypatch):
    """Cancellation during the success-path delete that outlives the grace
    period propagates without the failure handler launching another delete."""
    import app.services.evidence_storage as es

    monkeypatch.setattr(es, "CLEANUP_GRACE_SECONDS", 0.05)
    eid = str(uuid.uuid4())
    payload = b"post-copy cancellation"
    staging = s3_staging_key(eid, 2)
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest(), 2)
    release = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("delete_object", staging): release})

    task = asyncio.create_task(storage.put(eid, _chunks(payload), attempt_no=2))
    await until(lambda: ("delete_object", staging) in state.parked)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.parked.count(("delete_object", staging)) == 1  # no second delete
    assert ops(state, "delete_object") == []  # the first one is still parked
    assert final in state.objects and state.objects[final].data == payload
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert ops(state, "delete_object") == [staging]
    assert staging not in state.objects
    assert final in state.objects  # a final object is never deleted


async def test_s3_repeated_cancellation_keeps_client_open_until_cleanup_finishes():
    eid = str(uuid.uuid4())
    staging = s3_staging_key(eid, 1)
    release = asyncio.Event()
    state = FakeS3State()
    storage, _ = fake_s3_storage(state, park={("abort_multipart_upload", staging): release})

    async def capped():
        yield b"first"
        raise SiteLogTooLarge()

    task = asyncio.create_task(storage.put(eid, capped(), attempt_no=1))
    await until(lambda: ("abort_multipart_upload", staging) in state.parked)
    task.cancel()
    for _ in range(3):
        await asyncio.sleep(0)
    task.cancel()  # repeated cancellation while the cleanup is supervised
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.aborts == [(staging, "fake-upload-id-1")]
    assert state.uploads == {}
    order = [op for op, _ in state.calls]
    assert order.index("abort_multipart_upload") < order.index("client_exit")


async def _nothing():
    return
    yield b""  # pragma: no cover - makes this an async generator


async def test_s3_empty_payload_aborts_exactly_once_and_keeps_its_message(caplog):
    """A zero-byte payload cannot complete a multipart upload: the adapter
    raises 'empty payload' (unclassified) and the handler aborts the upload
    exactly once — also when that abort itself fails (warning, no masking)."""
    storage, state = fake_s3_storage()
    eid = str(uuid.uuid4())
    with pytest.raises(EvidenceStorageError) as info:
        await storage.put(eid, _nothing(), attempt_no=1)
    assert str(info.value) == "empty payload"
    assert info.value.failure_class is None
    assert state.aborts == [(s3_staging_key(eid, 1), "fake-upload-id-1")]
    assert ops(state, "abort_multipart_upload") == [s3_staging_key(eid, 1)]
    assert ops(state, "delete_object") == [] and ops(state, "copy_object:dest") == []

    failing, state2 = fake_s3_storage(cleanup_fail=True)
    with caplog.at_level("WARNING"), pytest.raises(EvidenceStorageError) as info2:
        await failing.put(eid, _nothing(), attempt_no=2)
    assert str(info2.value) == "empty payload"
    assert ops(state2, "abort_multipart_upload") == [s3_staging_key(eid, 2)]
    assert any("staging cleanup failed" in r.getMessage() for r in caplog.records)
