"""Recording, in-memory fake of the aioboto3 S3 client for storage tests.

Base-compatible on purpose: it implements only the client surface the
adapter calls (multipart create / upload / complete / abort, head / copy /
delete / get object) and raises REAL ``botocore.exceptions.ClientError``
instances shaped like the SDK's, so one fake drives both the fail-first
defect tests on the recorded base (723bb6e) and the acceptance tests on
the fixed adapter. No network, no new dependency (botocore is pinned via
aioboto3).

Modelled state (``FakeS3State``, shared by every client the factory hands
out so concurrent ``put`` calls see one bucket):

* ``objects``: key → ``FakeObject(data, version)``; ``version`` counts the
  writes to that key, so an overwrite is observable as identity change even
  when the bytes are identical;
* multipart uploads accumulate parts until ``complete`` materialises the
  staging object;
* ``calls`` / ``aborts`` record every operation and key.

Fault injection per client: ``fail_on`` (RuntimeError on that operation —
the A2a fake's behaviour), ``errors`` (operation → exception instance),
``cleanup_fail`` (abort and delete raise), ``final_exists`` (HEAD reports
the final key present), ``enter_error`` (raised on ``__aenter__``),
``stream_error`` (raised by ``Body.read`` after ``stream_error_after``
successful reads) and ``park`` ((operation, key) → ``asyncio.Event`` awaited
before that operation runs — the key for ``copy_object`` is the SOURCE
staging key, which is unique per attempt).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from botocore.exceptions import ClientError


def client_error(code: str, status: int, operation: str = "HeadObject") -> ClientError:
    """A botocore ``ClientError`` shaped exactly like the SDK raises it."""
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"fake {code}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


@dataclass
class FakeObject:
    data: bytes
    version: int


@dataclass
class FakeS3State:
    objects: dict[str, FakeObject] = field(default_factory=dict)
    uploads: dict[str, bytearray] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    aborts: list[tuple[str, str]] = field(default_factory=list)
    parked: list[tuple[str, str]] = field(default_factory=list)
    upload_seq: int = 0


class FakeBody:
    def __init__(self, data: bytes, error: BaseException | None, error_after: int):
        self._data = data
        self._pos = 0
        self._error = error
        self._error_after = error_after
        self._reads = 0

    async def read(self, n: int) -> bytes:
        if self._error is not None and self._reads >= self._error_after:
            raise self._error
        self._reads += 1
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class FakeS3Client:
    def __init__(
        self,
        state: FakeS3State,
        *,
        fail_on: str | None = None,
        errors: dict[str, BaseException] | None = None,
        cleanup_fail: bool = False,
        final_exists: bool = False,
        enter_error: BaseException | None = None,
        stream_error: BaseException | None = None,
        stream_error_after: int = 0,
        park: dict[tuple[str, str], asyncio.Event] | None = None,
    ):
        self._state = state
        self._fail_on = fail_on
        self._errors = errors or {}
        self._cleanup_fail = cleanup_fail
        self._final_exists = final_exists
        self._enter_error = enter_error
        self._stream_error = stream_error
        self._stream_error_after = stream_error_after
        self._park = park or {}

    # -- context -----------------------------------------------------------
    async def __aenter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self

    async def __aexit__(self, *exc):
        self._state.calls.append(("client_exit", ""))
        return False

    # -- helpers -----------------------------------------------------------
    async def _gate(self, op: str, key: str) -> None:
        event = self._park.get((op, key))
        if event is not None:
            self._state.parked.append((op, key))
            await event.wait()

    def _record(self, op: str, key: str) -> None:
        self._state.calls.append((op, key))
        if self._fail_on == op:
            raise RuntimeError(f"injected failure in {op}")
        if op in self._errors:
            raise self._errors[op]

    # -- multipart ---------------------------------------------------------
    async def create_multipart_upload(self, *, Bucket, Key):
        await self._gate("create_multipart_upload", Key)
        self._record("create_multipart_upload", Key)
        self._state.upload_seq += 1
        upload_id = f"fake-upload-id-{self._state.upload_seq}"
        self._state.uploads[upload_id] = bytearray()
        return {"UploadId": upload_id}

    async def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        await self._gate("upload_part", Key)
        self._record("upload_part", Key)
        self._state.uploads[UploadId].extend(Body)
        return {"ETag": f"etag-{PartNumber}"}

    async def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        await self._gate("complete_multipart_upload", Key)
        self._record("complete_multipart_upload", Key)
        data = bytes(self._state.uploads.pop(UploadId))
        prev = self._state.objects.get(Key)
        self._state.objects[Key] = FakeObject(data, (prev.version if prev else 0) + 1)
        return {}

    async def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        await self._gate("abort_multipart_upload", Key)
        self._state.aborts.append((Key, UploadId))
        self._record("abort_multipart_upload", Key)
        if self._cleanup_fail:
            raise RuntimeError("injected cleanup failure in abort")
        self._state.uploads.pop(UploadId, None)
        return {}

    # -- objects -----------------------------------------------------------
    async def head_object(self, *, Bucket, Key):
        await self._gate("head_object", Key)
        self._record("head_object", Key)
        if self._final_exists or Key in self._state.objects:
            return {}
        raise client_error("404", 404, "HeadObject")

    async def copy_object(self, *, Bucket, Key, CopySource):
        source = CopySource["Key"]
        await self._gate("copy_object", source)
        self._record("copy_object:dest", Key)
        self._record("copy_object:source", source)
        src = self._state.objects.get(source)
        if src is None:
            raise client_error("NoSuchKey", 404, "CopyObject")
        prev = self._state.objects.get(Key)
        self._state.objects[Key] = FakeObject(src.data, (prev.version if prev else 0) + 1)
        return {}

    async def delete_object(self, *, Bucket, Key):
        await self._gate("delete_object", Key)
        self._record("delete_object", Key)
        if self._cleanup_fail:
            raise RuntimeError("injected cleanup failure in delete")
        self._state.objects.pop(Key, None)
        return {}

    async def get_object(self, *, Bucket, Key):
        await self._gate("get_object", Key)
        self._record("get_object", Key)
        obj = self._state.objects.get(Key)
        if obj is None:
            raise client_error("NoSuchKey", 404, "GetObject")
        return {"Body": FakeBody(obj.data, self._stream_error, self._stream_error_after)}


def fake_s3_storage(state: FakeS3State | None = None, **client_kw):
    """An ``S3EvidenceStorage`` whose client factory returns fakes bound to
    ``state`` (a fresh state when omitted). Returns ``(storage, state)``."""
    from app.services.evidence_storage import S3EvidenceStorage

    state = state if state is not None else FakeS3State()
    storage = S3EvidenceStorage(
        endpoint_url="http://storage.invalid.localdomain:1", bucket="test-bucket"
    )
    storage._client = lambda: FakeS3Client(state, **client_kw)  # type: ignore[method-assign]
    return storage, state


def ops(state: FakeS3State, op: str) -> list[str]:
    return [k for o, k in state.calls if o == op]


async def until(predicate, *, tries: int = 2000) -> None:
    """Yield to the loop until ``predicate()`` holds (no sleeps, no timers)."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")
