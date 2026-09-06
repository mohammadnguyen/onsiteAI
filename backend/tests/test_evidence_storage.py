"""Contract tests for the evidence storage abstraction (local adapter).

The interface guarantees under test:

* put/open/exists roundtrip with verified size + sha256;
* immutable unique object key (evidence_id + content-hash prefix);
* no overwrite — a second put of identical content fails;
* chunked streaming — the adapter never receives (or needs) the whole
  payload at once, and readback yields bounded chunks;
* the interface exposes no delete (retention by construction).
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid

import pytest

from app.services.evidence_storage import (
    CHUNK_SIZE,
    EvidenceStorage,
    EvidenceStorageError,
    LocalEvidenceStorage,
    ObjectAlreadyExists,
    ObjectNotFound,
    make_object_key,
)

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes, size: int = CHUNK_SIZE):
    for i in range(0, len(payload), size):
        yield payload[i : i + size]


async def test_put_open_exists_roundtrip(tmp_path):
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


async def test_no_overwrite_same_content(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"immutable evidence"
    evidence_id = str(uuid.uuid4())

    await storage.put(evidence_id, _chunks(payload))
    with pytest.raises(ObjectAlreadyExists):
        await storage.put(evidence_id, _chunks(payload))


async def test_open_missing_key_raises(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    with pytest.raises(ObjectNotFound):
        storage.open("evidence/nope/deadbeef")


async def test_streaming_is_chunked_multi_chunk_payload(tmp_path):
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


async def test_failed_put_leaves_no_object_and_no_staging(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)

    async def exploding_chunks():
        yield b"partial"
        raise OSError("disk gone")

    with pytest.raises(EvidenceStorageError):
        await storage.put(str(uuid.uuid4()), exploding_chunks())

    staging = tmp_path / ".staging"
    assert not any(staging.glob("*")) if staging.exists() else True
    # No evidence/ tree was created for the failed put.
    assert not (tmp_path / "evidence").exists()


def test_interface_has_no_delete():
    """Retention by construction: the storage contract cannot delete.

    DEC-EVIDENCE-001 — this is the deterministic guarantee that no
    normal product path (or future extraction pipeline holding this
    interface) can destroy raw evidence. A privileged purge would be a
    separate promoted decision with its own restricted interface.
    """
    forbidden = [
        name
        for name in ("delete", "remove", "purge", "unlink", "overwrite")
        if hasattr(EvidenceStorage, name) or hasattr(LocalEvidenceStorage, name)
    ]
    assert forbidden == []


# ---------------------------------------------------------------------------
# WP A A1b: attempt-scoped staging discriminator.
# The discriminator isolates STAGING only; final Evidence identity
# (make_object_key) never sees it. Omitted => exact legacy behaviour.
# ---------------------------------------------------------------------------

from app.services.evidence_storage import (  # noqa: E402
    S3EvidenceStorage,
    local_staging_name,
    s3_staging_key,
    staging_suffix,
)


def test_staging_suffix_validation():
    assert staging_suffix(None) == ""
    assert staging_suffix(1) == ".a1"
    assert staging_suffix(42) == ".a42"
    for bad in (0, -1, True, False, "1", 1.5):
        with pytest.raises(ValueError):
            staging_suffix(bad)


def test_legacy_staging_paths_are_byte_identical_for_both_backends():
    """Omitted discriminator == the exact pre-A1b staging locations."""
    eid = str(uuid.uuid4())
    assert local_staging_name(eid, None) == f"{eid}.part"
    assert s3_staging_key(eid, None) == f"evidence/{eid}/.staging"


def test_attempt_staging_isolated_per_attempt_both_backends():
    eid = str(uuid.uuid4())
    local = {local_staging_name(eid, n) for n in (1, 2, 3)}
    local.add(local_staging_name(eid, None))
    assert len(local) == 4  # legacy + three attempts, all distinct
    s3 = {s3_staging_key(eid, n) for n in (1, 2, 3)}
    s3.add(s3_staging_key(eid, None))
    assert len(s3) == 4


async def test_final_key_unchanged_by_attempt(tmp_path):
    """The discriminator scopes staging only — final keys are identical
    to what the same content would produce without it."""
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"attempt-scoped staging, legacy final key"
    eid = str(uuid.uuid4())
    stored = await storage.put(eid, _chunks(payload), attempt_no=3)
    expected = make_object_key(eid, hashlib.sha256(payload).hexdigest())
    assert stored.key == expected
    assert ".a3" not in stored.key
    assert await storage.exists(stored.key)


async def test_same_content_collision_unchanged_with_attempts(tmp_path):
    """ObjectAlreadyExists semantics survive the discriminator: two
    attempts with identical bytes collide on the same final key."""
    storage = LocalEvidenceStorage(tmp_path)
    payload = b"identical bytes across attempts"
    eid = str(uuid.uuid4())
    await storage.put(eid, _chunks(payload), attempt_no=1)
    with pytest.raises(ObjectAlreadyExists):
        await storage.put(eid, _chunks(payload), attempt_no=2)


async def test_attempt_staging_observed_and_isolated_mid_stream(tmp_path):
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
        raise OSError("disk gone")

    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, exploding(), attempt_no=1)

    assert not (staging_dir / local_staging_name(eid, 1)).exists()
    assert survivor.read_bytes() == b"newer attempt in flight"
    assert not (tmp_path / "evidence").exists()


async def test_invalid_attempt_rejected_before_any_mutation(tmp_path):
    storage = LocalEvidenceStorage(tmp_path)
    eid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await storage.put(eid, _chunks(b"payload"), attempt_no=0)
    assert not (tmp_path / ".staging").exists()
    assert not (tmp_path / "evidence").exists()


async def test_s3_invalid_attempt_rejected_before_any_client_use():
    """Validation precedes the client context: no network is attempted."""
    storage = S3EvidenceStorage(
        endpoint_url="http://storage.invalid.localdomain:1", bucket="never"
    )
    with pytest.raises(ValueError):
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=-1)


async def test_no_payload_content_in_logs(tmp_path, caplog):
    storage = LocalEvidenceStorage(tmp_path)
    marker = b"SECRET-CAPTURE-CONTENT-MARKER"

    async def exploding():
        yield marker
        raise OSError("disk gone")

    with caplog.at_level("DEBUG"):
        with pytest.raises(EvidenceStorageError):
            await storage.put(str(uuid.uuid4()), exploding(), attempt_no=1)
        await storage.put(str(uuid.uuid4()), _chunks(marker), attempt_no=1)
    assert marker.decode() not in caplog.text


# ---------------------------------------------------------------------------
# WP A A1b follow-up: prove the ACTUAL S3 put() wires the attempt
# discriminator — the pure-helper tests above are not sufficient for that.
# A minimal recording fake stands in for the aioboto3 client; no new
# dependency, no network.
# ---------------------------------------------------------------------------


class _FakeS3Client:
    """Records every (operation, Key) the S3 put() path performs.

    ``fail_on``: operation name that raises; ``cleanup_fail``: abort and
    delete raise too (WP A A2a cleanup-never-masks tests);
    ``final_exists``: ``head_object`` reports the final key present.
    ``aborts`` records ``(Key, UploadId)`` of every abort call.
    """

    def __init__(
        self,
        calls: list[tuple[str, str]],
        *,
        fail_on: str | None = None,
        cleanup_fail: bool = False,
        final_exists: bool = False,
        aborts: list[tuple[str, str]] | None = None,
    ):
        self._calls = calls
        self._fail_on = fail_on
        self._cleanup_fail = cleanup_fail
        self._final_exists = final_exists
        self.aborts = aborts if aborts is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _record(self, op: str, key: str):
        self._calls.append((op, key))
        if self._fail_on == op:
            raise RuntimeError(f"injected failure in {op}")

    async def create_multipart_upload(self, *, Bucket, Key):
        self._record("create_multipart_upload", Key)
        return {"UploadId": "fake-upload-id"}

    async def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        self._record("upload_part", Key)
        return {"ETag": f"etag-{PartNumber}"}

    async def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        self._record("complete_multipart_upload", Key)
        return {}

    async def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self.aborts.append((Key, UploadId))
        self._record("abort_multipart_upload", Key)
        if self._cleanup_fail:
            raise RuntimeError("injected cleanup failure in abort")
        return {}

    async def head_object(self, *, Bucket, Key):
        self._record("head_object", Key)
        if self._final_exists:
            return {}
        raise KeyError("404: no such key")

    async def copy_object(self, *, Bucket, Key, CopySource):
        self._record("copy_object:dest", Key)
        self._record("copy_object:source", CopySource["Key"])
        return {}

    async def delete_object(self, *, Bucket, Key):
        self._record("delete_object", Key)
        if self._cleanup_fail:
            raise RuntimeError("injected cleanup failure in delete")
        return {}


def _fake_s3_storage(calls, **kw):
    storage = S3EvidenceStorage(
        endpoint_url="http://storage.invalid.localdomain:1", bucket="test-bucket"
    )
    storage._client = lambda: _FakeS3Client(calls, **kw)  # type: ignore[method-assign]
    return storage


async def test_s3_put_wires_attempt_scoped_staging_key():
    calls: list[tuple[str, str]] = []
    storage = _fake_s3_storage(calls)
    payload = b"s3 attempt wiring"
    eid = str(uuid.uuid4())

    stored = await storage.put(eid, _chunks(payload), attempt_no=2)

    expected_staging = f"evidence/{eid}/.staging.a2"
    legacy_staging = f"evidence/{eid}/.staging"
    expected_final = make_object_key(eid, hashlib.sha256(payload).hexdigest())

    staging_ops = {
        "create_multipart_upload",
        "upload_part",
        "complete_multipart_upload",
        "copy_object:source",
        "delete_object",
    }
    for op, key in calls:
        if op in staging_ops:
            assert key == expected_staging, (op, key)
    # No silent fallback: the legacy key never appears anywhere.
    assert all(key != legacy_staging for _, key in calls)
    # Final identity untouched by the discriminator.
    assert stored.key == expected_final
    assert ".a2" not in stored.key
    assert ("copy_object:dest", expected_final) in calls
    assert ("head_object", expected_final) in calls
    # Staging cleanup deleted exactly the current attempt's staging key.
    assert [k for op, k in calls if op == "delete_object"] == [expected_staging]


async def test_s3_put_legacy_path_uses_exact_legacy_staging_key():
    calls: list[tuple[str, str]] = []
    storage = _fake_s3_storage(calls)
    payload = b"s3 legacy wiring"
    eid = str(uuid.uuid4())

    stored = await storage.put(eid, _chunks(payload))

    legacy_staging = f"evidence/{eid}/.staging"
    for op, key in calls:
        if op in {
            "create_multipart_upload",
            "upload_part",
            "complete_multipart_upload",
            "copy_object:source",
            "delete_object",
        }:
            assert key == legacy_staging, (op, key)
    assert ".a" not in stored.key
    assert stored.key == make_object_key(
        eid, hashlib.sha256(payload).hexdigest()
    )


async def test_s3_failure_touches_only_current_attempt_staging_key():
    """A failing attempt-3 upload never touches attempt-2's staging key,
    the legacy key, or any final key."""
    calls: list[tuple[str, str]] = []
    storage = _fake_s3_storage(calls, fail_on="complete_multipart_upload")
    eid = str(uuid.uuid4())

    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(b"doomed bytes"), attempt_no=3)

    own_staging = f"evidence/{eid}/.staging.a3"
    assert calls, "fake client saw no calls"
    for _, key in calls:
        assert key == own_staging, key
    assert all(k != f"evidence/{eid}/.staging" for _, k in calls)
    assert all(k != f"evidence/{eid}/.staging.a2" for _, k in calls)


# ---------------------------------------------------------------------------
# WP A A2a — S3 staging cleanup contract (Revision 2.1 §5):
#   * failure BEFORE completion → abort THIS attempt's multipart upload
#     (exact staging key + upload id); never a delete;
#   * failure AFTER completion (copy / collision) → delete THIS attempt's
#     staging object; never an abort;
#   * a cleanup failure never masks the primary error;
#   * a failed delete after a valid final object still succeeds;
#   * delete_object never receives a final content-addressed key;
#   * create_multipart_upload failing → nothing to abort, nothing deleted.
# ---------------------------------------------------------------------------


def _ops(calls, op):
    return [k for o, k in calls if o == op]


async def test_s3_pre_completion_failure_aborts_exact_attempt():
    calls, aborts = [], []
    storage = _fake_s3_storage(calls, fail_on="upload_part", aborts=aborts)
    eid = str(uuid.uuid4())
    with pytest.raises(EvidenceStorageError) as info:
        await storage.put(eid, _chunks(b"doomed"), attempt_no=4)
    assert "injected failure in upload_part" in str(info.value)
    staging = f"evidence/{eid}/.staging.a4"
    assert aborts == [(staging, "fake-upload-id")]
    assert _ops(calls, "delete_object") == []
    assert all(k == staging for _, k in calls)


async def test_s3_post_completion_failure_deletes_staging_not_final():
    calls, aborts = [], []
    storage = _fake_s3_storage(calls, fail_on="copy_object:dest", aborts=aborts)
    eid = str(uuid.uuid4())
    payload = b"copy fails"
    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(payload), attempt_no=1)
    staging = f"evidence/{eid}/.staging.a1"
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest())
    assert aborts == []
    assert _ops(calls, "delete_object") == [staging]
    assert final not in _ops(calls, "delete_object")


async def test_s3_collision_deletes_own_staging_never_final():
    calls, aborts = [], []
    storage = _fake_s3_storage(calls, final_exists=True, aborts=aborts)
    eid = str(uuid.uuid4())
    payload = b"identical bytes"
    with pytest.raises(ObjectAlreadyExists) as info:
        await storage.put(eid, _chunks(payload), attempt_no=2)
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest())
    assert str(info.value) == final
    assert aborts == []
    assert _ops(calls, "delete_object") == [f"evidence/{eid}/.staging.a2"]
    assert _ops(calls, "copy_object:dest") == []  # no second copy over final


async def test_s3_cleanup_failure_never_masks_primary_error(caplog):
    calls = []
    storage = _fake_s3_storage(calls, fail_on="upload_part", cleanup_fail=True)
    with caplog.at_level("WARNING"), pytest.raises(EvidenceStorageError) as info:
        await storage.put(str(uuid.uuid4()), _chunks(b"x"), attempt_no=1)
    assert "injected failure in upload_part" in str(info.value)
    assert "cleanup" not in str(info.value)
    assert any("staging cleanup failed" in r.getMessage() for r in caplog.records)
    # post-completion variant
    calls2 = []
    storage2 = _fake_s3_storage(calls2, fail_on="copy_object:dest", cleanup_fail=True)
    with pytest.raises(EvidenceStorageError) as info2:
        await storage2.put(str(uuid.uuid4()), _chunks(b"y"), attempt_no=1)
    assert "injected failure in copy_object:dest" in str(info2.value)


async def test_s3_delete_failure_after_valid_final_still_succeeds(caplog):
    calls = []
    storage = _fake_s3_storage(calls, cleanup_fail=True)
    eid = str(uuid.uuid4())
    payload = b"final is valid"
    with caplog.at_level("WARNING"):
        stored = await storage.put(eid, _chunks(payload), attempt_no=3)
    assert stored.key == make_object_key(eid, hashlib.sha256(payload).hexdigest())
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert _ops(calls, "delete_object") == [f"evidence/{eid}/.staging.a3"]
    assert any("cleanup failed after valid final object" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "fail_on, final_exists",
    [(None, False), ("upload_part", False), ("complete_multipart_upload", False),
     ("copy_object:dest", False), (None, True), ("create_multipart_upload", False)],
)
async def test_s3_delete_never_receives_final_key(fail_on, final_exists):
    calls, aborts = [], []
    storage = _fake_s3_storage(
        calls, fail_on=fail_on, final_exists=final_exists, aborts=aborts
    )
    eid = str(uuid.uuid4())
    payload = b"never delete final"
    final = make_object_key(eid, hashlib.sha256(payload).hexdigest())
    with contextlib.suppress(EvidenceStorageError):
        await storage.put(eid, _chunks(payload), attempt_no=1)
    for key in _ops(calls, "delete_object") + [k for k, _ in aborts]:
        assert key == f"evidence/{eid}/.staging.a1"
        assert key != final
    if fail_on == "create_multipart_upload":
        assert aborts == [] and _ops(calls, "delete_object") == []


async def test_s3_legacy_path_abort_uses_legacy_key():
    calls, aborts = [], []
    storage = _fake_s3_storage(calls, fail_on="upload_part", aborts=aborts)
    eid = str(uuid.uuid4())
    with pytest.raises(EvidenceStorageError):
        await storage.put(eid, _chunks(b"legacy"))
    assert aborts == [(f"evidence/{eid}/.staging", "fake-upload-id")]
