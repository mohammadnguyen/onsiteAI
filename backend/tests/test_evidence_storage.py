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
