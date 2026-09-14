"""Evidence object-storage abstraction (DEC-EVIDENCE-001).

The interface is deliberately ``put`` / ``open`` / ``exists`` only:

* **No delete.** Raw evidence is never destroyed by any normal product
  path. The absence of a delete method is the retention guarantee — a
  deterministic boundary below any prompt or review step (ADR-001 §4).
  A future privileged purge (legal/compliance/tenant destruction) is a
  separate promoted decision with its own restricted interface and
  immutable audit; it is not designed and not implemented here.
* **No overwrite.** ``put`` writes an immutable unique object key and
  fails with :class:`ObjectAlreadyExists` if the key already exists.

Object keys — contract history (provenance kept on purpose):

* Evidence foundation slice (5d9c0f7): ``evidence/{evidence_id}/{sha256[:16]}``.
* WP A A1b (PR #11, e302933): ``attempt_no`` introduced as a STAGING-only
  discriminator; the ruling then was "the final key is unchanged by the
  attempt".
* WP-S-core (founder decision FD1, 2026-09-14): that ruling is reversed for
  attempt-numbered uploads. :func:`make_object_key` yields
  ``evidence/{evidence_id}/{sha256[:16]}.a{N}`` when a positive ``attempt_no``
  is passed; ``attempt_no=None`` — what every legacy caller passes, the
  ``/evidence`` upload path (``services/evidence.py: create_evidence``)
  included — yields the byte-identical legacy key. Existing objects are read
  by the key stored on their Evidence row — nothing is migrated or
  rewritten. Accepted consequence: two attempts storing identical bytes
  produce two distinct objects; the unreferenced one is residue that
  read-only tooling may enumerate and nothing deletes.
* Known stale references left for the slices that own them (recorded, not
  silent): ``services/site_log.py: upload_attachment`` still describes an
  ``ObjectAlreadyExists`` as "identical bytes to an earlier attempt" and
  adopts the existing object — under FD1 a collision can only be a
  same-``(evidence_id, attempt_no)`` re-put (e.g. after a database restore
  rewound the attempt counter), and that adoption branch is removed by
  A2a.2 (design A5); ``models/evidence.py`` documents the key as
  "evidence_id + content-hash prefix" without the ``.a{N}`` form.

Why attempt-scoped keys: a stale writer for attempt N-1 can publish only at
its own key, so it can never overwrite — or be adopted onto — the key a
newer attempt confirmed. That isolation holds by key construction alone. It
is the ONLY guarantee claimed here: two concurrent ``put`` calls for the
SAME ``(evidence_id, attempt_no)`` are not made atomic by the adapter (the
S3 path is HEAD-then-copy); the Site Log service never hands the same
attempt to two callers (a pending row answers ``SiteLogUploadInProgress``
under the manifest-row lock, and the attempt number only ever increments),
and a *sequential* re-put of an existing key is rejected by the HEAD /
exists check with the object left untouched.

Error classification (WP-S(1)):

* :class:`ObjectNotFound` — the key is absent (HTTP 404 / ``NoSuchKey`` /
  ``NotFound``, or a missing local file). Only a confirmed absence maps here.
* :class:`StoragePermanentError` — the backend refused or is misconfigured
  (401/403/``AccessDenied``, missing or invalid credentials, unknown bucket,
  permanent redirect, TLS certificate failure, invalid endpoint, local
  permission denied). Retrying without operator action will not help.
  ``put`` never proceeds to the destination copy after such a HEAD.
* :class:`StorageTransientError` — connectivity, timeouts, throttling, 5xx,
  interrupted response streams, local I/O errors. Retryable.
* :class:`EvidenceStorageError` (base, ``failure_class=None``) — anything the
  classifier does not recognise. It is raised, never swallowed, and never
  reinterpreted as "absent".

Every failure surface is covered — client construction and context entry,
HEAD, ``exists``, GET, the response stream, the local filesystem. Honest
limits of the S3 probes: ``exists`` returns ``False`` only for a HEAD 404,
and S3 answers a body-less 404 for a missing bucket as well as for a
missing key (a misspelled bucket surfaces as ``NoSuchBucket`` → permanent on
the first ``put``); and the pre-copy HEAD needs credentials for which HEAD
on a missing key returns 404 rather than 403 (``s3:ListBucket`` on AWS-style
policies) — with read/write-only credentials every ``put`` would now fail
permanently instead of silently proceeding as before WP-S(1). That
precondition is a deployment check (one ``head_object`` on a guaranteed-
missing key with the configured credentials must yield ``404``), UNVERIFIED
on Tigris until recorded.

Cleanup on failure (WP-S(4)): whatever interrupts a ``put`` — a backend
error, an exception raised by the chunk SOURCE (``SiteLogTooLarge`` /
``EvidenceTooLarge`` from the size caps, an I/O error while the request
body is read, ...) or task cancellation — the adapter removes only THIS
call's staging state (the ``.part`` file, the multipart upload, or the
completed staging object). Source exceptions and cancellation are then
re-raised unchanged (same object, same type); backend errors are re-raised
as a classified :class:`EvidenceStorageError` (never a raw SDK/OS
exception); a cleanup failure is logged content-free and never masks the
primary error; a secondary adapter failure raised while unwinding a source
failure is logged and the source exception stays the one raised. A final
(content-addressed) object is never deleted by any path in this module.
Honest limit: a cancellation that lands while the cleanup call itself is
in flight (abort / delete over the network) interrupts that cleanup and
replaces the primary exception — the multipart upload or staging object
is then left for the bucket lifecycle policy (the existing infra gate for
incomplete multipart uploads); no final object is affected.
Caller note (a change from the base): before WP-S-core the S3 adapter
wrapped EVERY source exception into ``EvidenceStorageError`` and the local
adapter wrapped a source ``OSError``, so the upload services marked the row
``failed`` (audit reason ``storage_error``, HTTP 502). Now only the two
size-cap exceptions are handled by those services; any other source
exception (an I/O error reading the spooled request body — the only
producer on the current API, since the body is fully spooled before the
handler runs) reaches the API as an unhandled 500 and leaves the
Evidence/attachment row ``pending`` — for a Site Log attachment that means
409 on retry until the admin reset path (RESET_MIN_AGE) frees it. The
failed-transition bookkeeping for that case is A2a.2 scope (design B6);
the consequence is recorded here so it is not mistaken for adapter
behaviour. Also recorded: ``services/site_log.py`` calls ``exists`` /
``open`` inside its ``except ObjectAlreadyExists`` handler; a classified
error raised there is not routed through ``_fail_attachment`` (same shape as
the pre-existing ``ObjectNotFound`` case; reachable only through the
same-attempt collision described above; closed when A2a.2 removes the
adoption branch).

Two implementations:

* :class:`LocalEvidenceStorage` — filesystem, the default in
  development and tests (deterministic, no external services).
* :class:`S3EvidenceStorage` — any S3-compatible endpoint (Tigris in
  staging/production). Uploads stream through a bounded staging key and
  are finished with a server-side copy to the final key; the staging
  object is internal pre-commit state, not stored evidence.

Both stream in fixed-size chunks with an incremental sha256 — the full
payload is never held in application memory (512MB VM constraint).

Module size (CLAUDE.md soft limit ~400 lines): this module is accepted
above the limit for WP-S-core only because the error-classification tables
must sit next to the raise sites that apply them and both adapters share
one retention boundary that ``test_interface_has_no_delete`` introspects
as a whole. Splitting the classification into its own module (re-exported
from here) is the first structural step if the module grows again, e.g.
with WP-S-hardening (2) metadata or (5) checker hooks.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

CHUNK_SIZE = 1024 * 1024  # 1 MiB read/write granularity


# Content-free by rule: this module logs keys, upload ids and exception
# classes only — never payload bytes or client-supplied names.
logger = logging.getLogger(__name__)


FailureClass = Literal["transient", "permanent"]
Classification = Literal["absent", "transient", "permanent", "unclassified"]


class EvidenceStorageError(RuntimeError):
    """Raised when the storage backend fails to persist or read bytes.

    ``failure_class`` is ``"transient"``, ``"permanent"`` or ``None``
    (unclassified). The subclasses pin it; callers may branch on the
    attribute without importing the subclasses.
    """

    failure_class: FailureClass | None = None


class StorageTransientError(EvidenceStorageError):
    """Backend failure that a later retry may clear (network, 5xx, I/O)."""

    failure_class = "transient"


class StoragePermanentError(EvidenceStorageError):
    """Backend refusal or misconfiguration; retrying alone will not help."""

    failure_class = "permanent"


class ObjectAlreadyExists(EvidenceStorageError):
    """Raised on an attempt to write to an existing object key."""


class ObjectNotFound(EvidenceStorageError):
    """Raised when opening a key that does not exist (confirmed absence)."""


@dataclass(frozen=True)
class StoredObject:
    """Result of a completed ``put``: the immutable key plus verified facts."""

    key: str
    size_bytes: int
    sha256: str


def attempt_suffix(attempt_no: int | None) -> str:
    """Attempt discriminator ``".a{N}"`` — staging AND final keys.

    ``None`` (what every legacy caller passes) yields the empty suffix, so
    every existing caller — the ``/evidence`` upload path included — keeps
    its exact legacy staging location and legacy final key. A positive
    integer isolates that attempt's staging target and, since WP-S-core
    (FD1), its final key too.

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


# A1b public name kept as an alias for API stability; no in-repo callers today.
staging_suffix = attempt_suffix


def make_object_key(
    evidence_id: str, sha256_hex: str, attempt_no: int | None = None
) -> str:
    """Immutable unique object key.

    * ``attempt_no=None`` → ``evidence/{evidence_id}/{sha256[:16]}`` — the
      legacy key, byte-identical to every object written before WP-S-core.
    * positive ``attempt_no`` → ``evidence/{evidence_id}/{sha256[:16]}.a{N}``
      (FD1, 2026-09-14): one key per upload attempt, so no attempt can
      publish at, overwrite, or be adopted onto another attempt's key.

    Invalid ``attempt_no`` values raise ``ValueError`` (see
    :func:`attempt_suffix`).
    """
    return f"evidence/{evidence_id}/{sha256_hex[:16]}{attempt_suffix(attempt_no)}"


def local_staging_name(evidence_id: str, attempt_no: int | None) -> str:
    """Local staging filename: ``{evidence_id}[.aN].part``."""
    return f"{evidence_id}{attempt_suffix(attempt_no)}.part"


def s3_staging_key(evidence_id: str, attempt_no: int | None) -> str:
    """S3 staging key: ``evidence/{evidence_id}/.staging[.aN]``."""
    return f"evidence/{evidence_id}/.staging{attempt_suffix(attempt_no)}"


# ------------------------------------------------------------ classification

# HTTP status / S3 error codes. Anything not listed is "unclassified": it is
# still raised, just without a retry class — never treated as absent.
_ABSENT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_PERMANENT_CODES = frozenset(
    {
        "401",
        "403",
        "AccessDenied",
        "AccessDeniedException",
        "AllAccessDisabled",
        "AccountProblem",
        "AuthorizationHeaderMalformed",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidToken",
        "ExpiredToken",
        "NoSuchBucket",
        "InvalidBucketName",
        "PermanentRedirect",
        "301",
    }
)
_TRANSIENT_CODES = frozenset(
    {
        "500",
        "502",
        "503",
        "504",
        "InternalError",
        "ServiceUnavailable",
        "SlowDown",
        "RequestTimeout",
        "Throttling",
        "ThrottlingException",
        "RequestLimitExceeded",
        "TooManyRequests",
    }
)
_PERMANENT_STATUSES = frozenset({301, 401, 403})
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# botocore / aiohttp / ssl exception class names, matched across the MRO of
# the exception and of its cause chain (duck-typed so this table works
# without importing the SDK at module import time and so tests can use the
# real classes). Certificate failures are configuration, not weather.
_PERMANENT_EXCEPTIONS = frozenset(
    {
        "NoCredentialsError",
        "PartialCredentialsError",
        "UnknownCredentialError",
        "NoRegionError",
        "InvalidRegionError",
        "UnknownEndpointError",
        "EndpointResolutionError",
        "InvalidEndpointConfigurationError",
        "UnsupportedS3ConfigurationError",
        "UnknownServiceError",
        "ParamValidationError",
        "SSLCertVerificationError",
        "ClientConnectorCertificateError",
        "ClientConnectorSSLError",
        "CertificateError",
        "PermissionError",
        "FileNotFoundError",
    }
)
_TRANSIENT_EXCEPTIONS = frozenset(
    {
        "EndpointConnectionError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "ConnectionClosedError",
        "ResponseStreamingError",
        "IncompleteReadError",
        "HTTPClientError",
        "ProxyConnectionError",
        "SSLError",
        "ClientSSLError",
        "ClientConnectorError",
        "ClientConnectionError",
        "ClientOSError",
        "ClientPayloadError",
        "ServerDisconnectedError",
        "ServerTimeoutError",
    }
)


def _chain(exc: BaseException):
    """The exception, then its wrapped/cause/context exceptions (bounded)."""
    seen: set[int] = set()
    todo: list[BaseException] = [exc]
    while todo and len(seen) < 16:
        cur = todo.pop(0)
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        kwargs = getattr(cur, "kwargs", None)
        wrapped = kwargs.get("error") if isinstance(kwargs, dict) else None
        for nxt in (wrapped, cur.__cause__, cur.__context__):
            if isinstance(nxt, BaseException):
                todo.append(nxt)


def classify_storage_exception(exc: BaseException) -> Classification:
    """Map a backend exception to absent / transient / permanent / unclassified.

    Order: botocore ``ClientError`` shape (``.response`` carrying
    ``Error.Code`` and ``ResponseMetadata.HTTPStatusCode``; the code is more
    specific than the status), then class names across the MRO of the
    exception and its cause chain (permanent before transient, so a TLS
    certificate failure wrapped in a generic ``SSLError`` stays permanent),
    then the builtin network / OS exceptions. Anything else is
    ``"unclassified"``.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        code = str(error.get("Code", "")) if isinstance(error, dict) else ""
        meta = response.get("ResponseMetadata") or {}
        status = meta.get("HTTPStatusCode") if isinstance(meta, dict) else None
        # The S3 error code is more specific than the HTTP status (a missing
        # bucket is a 404 too on GET/PUT, but it is misconfiguration, not an
        # absent key; body-less HEAD responses carry only the status code).
        if code in _ABSENT_CODES:
            return "absent"
        if code in _PERMANENT_CODES:
            return "permanent"
        if code in _TRANSIENT_CODES:
            return "transient"
        if status == 404:
            return "absent"
        if status in _PERMANENT_STATUSES:
            return "permanent"
        if status in _TRANSIENT_STATUSES:
            return "transient"
        return "unclassified"
    names = {cls.__name__ for cur in _chain(exc) for cls in type(cur).__mro__}
    if names & _PERMANENT_EXCEPTIONS:
        return "permanent"
    if names & _TRANSIENT_EXCEPTIONS:
        return "transient"
    if isinstance(exc, TimeoutError | ConnectionError):
        return "transient"
    if isinstance(exc, OSError):
        return "transient"
    return "unclassified"


def _wrap_backend_error(exc: BaseException, *, op: str) -> EvidenceStorageError:
    """Classified error for an SDK exception. Message is content-free
    (operation, class name, the SDK's own message — never payload)."""
    cls = classify_storage_exception(exc)
    msg = f"{op} failed [{cls}]: {type(exc).__name__}: {exc}"
    if cls == "permanent":
        return StoragePermanentError(msg)
    if cls in ("transient", "absent"):
        # "absent" outside a probe means this call's own pre-commit state
        # vanished under it (e.g. staging expired): retryable, not a 404.
        return StorageTransientError(msg)
    return EvidenceStorageError(msg)


_PERMANENT_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})


def _wrap_os_error(exc: OSError, *, op: str) -> EvidenceStorageError:
    """Classified error for the local adapter's own filesystem failures."""
    msg = f"{op} failed: {type(exc).__name__}: {exc}"
    if isinstance(exc, PermissionError) or exc.errno in _PERMANENT_ERRNOS:
        return StoragePermanentError(msg)
    return StorageTransientError(msg)


class _SourceGuard:
    """Async-iterator wrapper recording the exception raised by the chunk
    SOURCE (the request body / size cap), as opposed to the adapter's own
    I/O, so ``put`` can re-raise source exceptions unchanged (WP-S(4))."""

    def __init__(self, chunks: AsyncIterator[bytes]):
        self._it = chunks.__aiter__()
        self.exc: BaseException | None = None

    def __aiter__(self) -> _SourceGuard:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._it.__anext__()
        except StopAsyncIteration:
            raise
        except BaseException as exc:
            self.exc = exc
            raise


def _primary(exc: BaseException, source: _SourceGuard, *, op: str) -> BaseException | None:
    """The exception ``put`` must re-raise UNCHANGED, or ``None`` when
    ``exc`` is the adapter's own failure and must be classified.

    Unchanged: the source exception (even when a secondary adapter failure
    was raised while unwinding it — logged, then the source stays primary),
    an exception that already is a storage error, and anything that is not
    an ``Exception`` (cancellation, interpreter exit).
    """
    if source.exc is not None:
        if exc is not source.exc:
            logger.warning(
                "%s: secondary failure while unwinding a source failure error=%s",
                op,
                type(exc).__name__,
            )
        return source.exc
    if isinstance(exc, EvidenceStorageError) or not isinstance(exc, Exception):
        return exc
    return None


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
    pre-commit state; on ANY failure (backend, source, cancellation) it is
    removed (that is not evidence deletion — the object was never stored).
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
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _wrap_os_error(exc, op="put:staging_dir") from exc
        staging = staging_dir / staging_name

        hasher = hashlib.sha256()
        size = 0
        source = _SourceGuard(chunks)
        try:
            with staging.open("wb") as fh:
                async for chunk in source:
                    hasher.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)

            sha = hasher.hexdigest()
            key = make_object_key(evidence_id, sha, attempt_no)
            final = self._path(key)
            if final.exists():
                raise ObjectAlreadyExists(key)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            return StoredObject(key=key, size_bytes=size, sha256=sha)
        except BaseException as exc:
            self._discard_staging(staging)
            primary = _primary(exc, source, op="local put")
            if primary is exc:
                raise
            if primary is not None:
                # The source exception is re-raised as itself; the secondary
                # adapter failure stays attached as its __context__ (logged).
                raise primary  # noqa: B904
            if isinstance(exc, OSError):
                raise _wrap_os_error(exc, op="put") from exc
            raise

    @staticmethod
    def _discard_staging(staging: Path) -> None:
        """Remove THIS call's ``.part`` file; a failure here is logged
        content-free and never masks the primary error."""
        try:
            staging.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning(
                "local staging cleanup failed staging=%s error=%s",
                staging.name,
                type(cleanup_exc).__name__,
            )

    async def _iter_file(self, path: Path, key: str) -> AsyncIterator[bytes]:
        try:
            fh = path.open("rb")
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc
        except OSError as exc:
            raise _wrap_os_error(exc, op="open") from exc
        with fh:
            while True:
                try:
                    chunk = fh.read(CHUNK_SIZE)
                except OSError as exc:
                    raise _wrap_os_error(exc, op="open:stream") from exc
                if not chunk:
                    return
                yield chunk

    def open(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        try:
            is_file = path.is_file()
        except OSError as exc:
            raise _wrap_os_error(exc, op="open") from exc
        if not is_file:
            raise ObjectNotFound(key)
        return self._iter_file(path, key)

    async def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except OSError as exc:
            raise _wrap_os_error(exc, op="exists") from exc


class S3EvidenceStorage:
    """S3-compatible adapter (Tigris in staging/production).

    Uses ``aioboto3`` — native async streaming under the async FastAPI
    stack, avoiding a thread per in-flight upload. (Pre-authorized
    fallback if aiobotocore pinning ever conflicts: boto3 +
    ``asyncio.to_thread`` with the same interface.)
    """

    backend_name = "s3"

    def __init__(self, endpoint_url: str, bucket: str):
        # Runtime import only: the dependency is needed when the s3 backend
        # is configured (staging/production); tests use botocore's exception
        # classes, which the aioboto3 dependency installs.
        import aioboto3

        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._bucket = bucket

    def _client(self):
        return self._session.client("s3", endpoint_url=self._endpoint_url)

    @contextlib.asynccontextmanager
    async def _connect(self, *, op: str):
        """Enter the SDK client context. Construction / entry failures are
        classified instead of escaping raw — an invalid endpoint or client
        configuration (``ValueError`` / ``TypeError`` from the SDK) is
        permanent; credential / connection errors raised lazily on the
        first request are classified by the per-operation handlers. An exit
        failure is logged, never raised."""
        try:
            cm = self._client()
            s3 = await cm.__aenter__()
        except (ValueError, TypeError) as exc:
            raise StoragePermanentError(
                f"{op}:client failed [permanent]: {type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            raise _wrap_backend_error(exc, op=f"{op}:client") from exc
        try:
            yield s3
        finally:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as exit_exc:  # botocore surface is broad
                logger.warning(
                    "s3 client exit failed op=%s error=%s",
                    op,
                    type(exit_exc).__name__,
                )

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
        source = _SourceGuard(chunks)
        async with self._connect(op="put") as s3:
            upload_id: str | None = None
            completed = False
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

                async for chunk in source:
                    hasher.update(chunk)
                    size += len(chunk)
                    buf.extend(chunk)
                    await _flush(final=False)
                await _flush(final=True)

                if not parts:
                    # A zero-byte payload cannot complete a multipart upload.
                    # No service-level zero-byte rejection exists today (the
                    # local adapter stores an empty object) — recorded, not
                    # changed here. The handler below aborts exactly once.
                    raise EvidenceStorageError("empty payload")

                await s3.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=staging_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
                completed = True  # cleanup boundary: abort → delete

                sha = hasher.hexdigest()
                key = make_object_key(evidence_id, sha, attempt_no)
                # Only a confirmed absence lets the copy proceed. 403 / 5xx /
                # anything unrecognised is a classified failure — the copy
                # never runs over an unknown destination state.
                try:
                    await s3.head_object(Bucket=self._bucket, Key=key)
                except Exception as exc:
                    if classify_storage_exception(exc) != "absent":
                        raise _wrap_backend_error(exc, op="head_object") from exc
                else:
                    raise ObjectAlreadyExists(key)
                await s3.copy_object(
                    Bucket=self._bucket,
                    Key=key,
                    CopySource={"Bucket": self._bucket, "Key": staging_key},
                )
                # Staging cleanup is internal pre-commit state removal,
                # not evidence deletion. The final object is confirmed
                # valid at this point: a cleanup failure is logged and the
                # store still succeeds — never downgraded to failed.
                try:
                    await s3.delete_object(Bucket=self._bucket, Key=staging_key)
                except Exception as cleanup_exc:  # botocore surface is broad
                    logger.warning(
                        "s3 staging cleanup failed after valid final object "
                        "staging_key=%s error=%s",
                        staging_key,
                        type(cleanup_exc).__name__,
                    )
                return StoredObject(key=key, size_bytes=size, sha256=sha)
            except BaseException as exc:
                # Cleanup THIS attempt's staging first, then re-raise: source
                # exceptions and cancellation unchanged, SDK errors classified.
                await self._cleanup_staging(s3, staging_key, upload_id, completed)
                primary = _primary(exc, source, op="s3 put")
                if primary is exc:
                    raise
                if primary is not None:
                    # Source exception re-raised as itself; the secondary
                    # failure stays attached as its __context__ (logged).
                    raise primary  # noqa: B904
                raise _wrap_backend_error(exc, op="put") from exc

    async def _cleanup_staging(
        self, s3, staging_key: str, upload_id: str | None, completed: bool
    ) -> None:
        """Best-effort cleanup of THIS attempt's staging only (WP A A2a).

        * before completion: abort the multipart upload (exact staging
          key + upload id);
        * after completion: delete the staging object.

        Never touches a final content-addressed key. Any cleanup failure
        is logged content-free and swallowed so the primary error is
        never masked. Hard process death cannot reach here — incomplete
        multipart residue is a bucket lifecycle policy (infra gate).
        """
        try:
            if completed:
                await s3.delete_object(Bucket=self._bucket, Key=staging_key)
            elif upload_id is not None:
                await s3.abort_multipart_upload(
                    Bucket=self._bucket, Key=staging_key, UploadId=upload_id
                )
        except Exception as cleanup_exc:  # botocore surface is broad
            logger.warning(
                "s3 staging cleanup failed staging_key=%s completed=%s error=%s",
                staging_key,
                completed,
                type(cleanup_exc).__name__,
            )

    async def _iter_object(self, key: str) -> AsyncIterator[bytes]:
        async with self._connect(op="open") as s3:
            try:
                obj = await s3.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if classify_storage_exception(exc) == "absent":
                    raise ObjectNotFound(key) from exc
                raise _wrap_backend_error(exc, op="get_object") from exc
            stream = obj["Body"]
            while True:
                try:
                    chunk = await stream.read(CHUNK_SIZE)
                except Exception as exc:
                    raise _wrap_backend_error(exc, op="get_object:stream") from exc
                if not chunk:
                    return
                yield chunk

    def open(self, key: str) -> AsyncIterator[bytes]:
        return self._iter_object(key)

    async def exists(self, key: str) -> bool:
        async with self._connect(op="exists") as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if classify_storage_exception(exc) == "absent":
                    return False
                raise _wrap_backend_error(exc, op="exists") from exc
            return True


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
