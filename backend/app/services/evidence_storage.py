"""Evidence object-storage abstraction (DEC-EVIDENCE-001).

The interface is deliberately ``put`` / ``open`` / ``exists`` only:

* **No delete.** Raw evidence is never destroyed by any normal product
  path. The absence of a delete method is the retention guarantee — a
  deterministic boundary below any prompt or review step (ADR-001 §4).
  A future privileged purge (legal/compliance/tenant destruction) is a
  separate promoted decision with its own restricted interface and
  immutable audit; it is not designed and not implemented here.
* **No overwrite.** ``put`` writes an immutable unique object key
  (``evidence/{evidence_id}/{sha256[:16]}``) and fails if the key
  already exists.

Two implementations:

* :class:`LocalEvidenceStorage` — filesystem, the default in
  development and tests (deterministic, no external services).
* :class:`S3EvidenceStorage` — any S3-compatible endpoint (Tigris in
  staging/production). Uploads stream through a bounded staging key and
  are finished with a server-side copy to the final content-hash key;
  the staging object is internal pre-commit state, not stored evidence.

Both stream in fixed-size chunks with an incremental sha256 — the full
payload is never held in application memory (512MB VM constraint).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CHUNK_SIZE = 1024 * 1024  # 1 MiB read/write granularity


class EvidenceStorageError(RuntimeError):
    """Raised when the storage backend fails to persist or read bytes."""


class ObjectAlreadyExists(EvidenceStorageError):
    """Raised on an attempt to write to an existing object key."""


class ObjectNotFound(EvidenceStorageError):
    """Raised when opening a key that does not exist."""


@dataclass(frozen=True)
class StoredObject:
    """Result of a completed ``put``: the immutable key plus verified facts."""

    key: str
    size_bytes: int
    sha256: str


def make_object_key(evidence_id: str, sha256_hex: str) -> str:
    """Immutable unique object key: evidence_id + content-hash prefix."""
    return f"evidence/{evidence_id}/{sha256_hex[:16]}"


def staging_suffix(attempt_no: int | None) -> str:
    """Attempt-scoped staging discriminator (WP A A1b).

    ``None`` (the default everywhere today) yields the empty suffix, so
    every existing caller — the Expense/Evidence upload path included —
    keeps its exact legacy staging location. A positive integer isolates
    that attempt's STAGING target only: the discriminator never reaches
    :func:`make_object_key`, so final Evidence identity is untouched.

    Validation happens here, before any storage mutation: anything other
    than ``None`` or a positive ``int`` (bools rejected — they are ints
    in Python) raises ``ValueError``.
    """
    if attempt_no is None:
        return ""
    if isinstance(attempt_no, bool) or not isinstance(attempt_no, int):
        raise ValueError("attempt_no must be a positive integer or None")
    if attempt_no < 1:
        raise ValueError("attempt_no must be a positive integer or None")
    return f".a{attempt_no}"


def local_staging_name(evidence_id: str, attempt_no: int | None) -> str:
    """Local staging filename: ``{evidence_id}[.aN].part``."""
    return f"{evidence_id}{staging_suffix(attempt_no)}.part"


def s3_staging_key(evidence_id: str, attempt_no: int | None) -> str:
    """S3 staging key: ``evidence/{evidence_id}/.staging[.aN]``."""
    return f"evidence/{evidence_id}/.staging{staging_suffix(attempt_no)}"


class EvidenceStorage(Protocol):
    """Storage backend contract. put / open / exists — nothing else."""

    backend_name: str

    async def put(
        self,
        evidence_id: str,
        chunks: AsyncIterator[bytes],
        *,
        attempt_no: int | None = None,
    ) -> StoredObject: ...

    def open(self, key: str) -> AsyncIterator[bytes]: ...

    async def exists(self, key: str) -> bool: ...


class LocalEvidenceStorage:
    """Filesystem adapter for development and tests.

    Streams to a temp file under ``<root>/.staging/`` while hashing, then
    atomically renames into the final key path. The staging file is
    pre-commit state; on failure it is removed (that is not evidence
    deletion — the object was never stored).
    """

    backend_name = "local"

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / key

    async def put(
        self,
        evidence_id: str,
        chunks: AsyncIterator[bytes],
        *,
        attempt_no: int | None = None,
    ) -> StoredObject:
        # Validate the discriminator BEFORE any storage mutation.
        staging_name = local_staging_name(evidence_id, attempt_no)
        staging_dir = self._root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging = staging_dir / staging_name

        hasher = hashlib.sha256()
        size = 0
        try:
            with staging.open("wb") as fh:
                async for chunk in chunks:
                    hasher.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)

            sha = hasher.hexdigest()
            key = make_object_key(evidence_id, sha)
            final = self._path(key)
            if final.exists():
                raise ObjectAlreadyExists(key)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            return StoredObject(key=key, size_bytes=size, sha256=sha)
        except ObjectAlreadyExists:
            staging.unlink(missing_ok=True)
            raise
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise EvidenceStorageError(str(exc)) from exc

    async def _iter_file(self, path: Path) -> AsyncIterator[bytes]:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    return
                yield chunk

    def open(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return self._iter_file(path)

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3EvidenceStorage:
    """S3-compatible adapter (Tigris in staging/production).

    Uses ``aioboto3`` — native async streaming under the async FastAPI
    stack, avoiding a thread per in-flight upload. (Pre-authorized
    fallback if aiobotocore pinning ever conflicts: boto3 +
    ``asyncio.to_thread`` with the same interface.)
    """

    backend_name = "s3"

    def __init__(self, endpoint_url: str, bucket: str):
        # Lazy import: the dependency is only needed when the s3 backend
        # is configured (staging/production), keeping dev/test light.
        import aioboto3

        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._bucket = bucket

    def _client(self):
        return self._session.client("s3", endpoint_url=self._endpoint_url)

    async def put(
        self,
        evidence_id: str,
        chunks: AsyncIterator[bytes],
        *,
        attempt_no: int | None = None,
    ) -> StoredObject:
        # Validate the discriminator BEFORE any storage mutation.
        staging_key = s3_staging_key(evidence_id, attempt_no)
        hasher = hashlib.sha256()
        size = 0
        async with self._client() as s3:
            try:
                mpu = await s3.create_multipart_upload(
                    Bucket=self._bucket, Key=staging_key
                )
                upload_id = mpu["UploadId"]
                parts = []
                part_number = 1
                # S3 multipart parts (except the last) must be >= 5 MiB.
                buf = bytearray()
                min_part = 5 * 1024 * 1024

                async def _flush(final: bool) -> None:
                    nonlocal part_number
                    if not buf or (not final and len(buf) < min_part):
                        return
                    resp = await s3.upload_part(
                        Bucket=self._bucket,
                        Key=staging_key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=bytes(buf),
                    )
                    parts.append(
                        {"ETag": resp["ETag"], "PartNumber": part_number}
                    )
                    part_number += 1
                    buf.clear()

                async for chunk in chunks:
                    hasher.update(chunk)
                    size += len(chunk)
                    buf.extend(chunk)
                    await _flush(final=False)
                await _flush(final=True)

                if not parts:
                    # Zero-byte payloads never reach here (service rejects
                    # empty uploads), but guard the API contract anyway.
                    await s3.abort_multipart_upload(
                        Bucket=self._bucket,
                        Key=staging_key,
                        UploadId=upload_id,
                    )
                    raise EvidenceStorageError("empty payload")

                await s3.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=staging_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )

                sha = hasher.hexdigest()
                key = make_object_key(evidence_id, sha)
                try:
                    await s3.head_object(Bucket=self._bucket, Key=key)
                    raise ObjectAlreadyExists(key)
                except ObjectAlreadyExists:
                    raise
                except Exception:
                    pass  # 404 — key free, proceed
                await s3.copy_object(
                    Bucket=self._bucket,
                    Key=key,
                    CopySource={"Bucket": self._bucket, "Key": staging_key},
                )
                # Staging cleanup is internal pre-commit state removal,
                # not evidence deletion.
                await s3.delete_object(Bucket=self._bucket, Key=staging_key)
                return StoredObject(key=key, size_bytes=size, sha256=sha)
            except (ObjectAlreadyExists, EvidenceStorageError):
                raise
            except Exception as exc:  # botocore error surface is broad
                raise EvidenceStorageError(str(exc)) from exc

    async def _iter_object(self, key: str) -> AsyncIterator[bytes]:
        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                raise ObjectNotFound(key) from exc
            stream = obj["Body"]
            while True:
                chunk = await stream.read(CHUNK_SIZE)
                if not chunk:
                    return
                yield chunk

    def open(self, key: str) -> AsyncIterator[bytes]:
        return self._iter_object(key)

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False


def get_evidence_storage() -> EvidenceStorage:
    """Build the configured adapter from settings (config-level switch)."""
    from app.config import get_settings

    s = get_settings()
    if s.evidence_storage_backend == "s3":
        return S3EvidenceStorage(
            endpoint_url=s.evidence_s3_endpoint_url,
            bucket=s.evidence_s3_bucket,
        )
    return LocalEvidenceStorage(s.evidence_local_root)
