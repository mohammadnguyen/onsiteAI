"""Object storage for Timeline attachments (PR 6 — Tigris via boto3).

Issues short-lived presigned URLs so photo bytes travel directly
between the mobile client and the S3-compatible bucket — never through
the FastAPI process (single small dyno + 15s client timeouts make
proxying multi-MB uploads a non-starter).

Tigris specifics (both are required or requests fail against the
Tigris endpoint):

* ``signature_version="s3v4"`` — presigned PUTs signed with the legacy
  default get access-denied.
* ``addressing_style="virtual"`` — bucket-in-hostname URLs.

Sync boto3 inside an async app is deliberate and safe here:
``generate_presigned_url`` is pure local computation (HMAC signing) —
it performs no network IO, so it cannot block the event loop.

Configuration comes from :mod:`app.config` (``AWS_ENDPOINT_URL_S3`` /
``BUCKET_NAME`` / ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` —
the exact names ``fly storage create`` injects). Non-dev environments
fail fast at settings construction when any are missing; in
development this module raises :class:`StorageNotConfigured` at first
use instead, so a laptop without a bucket still boots.

The HTTP layer (PR 7) maps :class:`StorageNotConfigured` to a 503 —
it is an operator configuration fault, not a client error.
"""

from __future__ import annotations

import re
import threading
import uuid

import boto3
from botocore.config import Config

from app.config import get_settings


class StorageNotConfigured(Exception):
    """Raised when attachment storage is used without configuration.

    Only reachable in development (non-dev fails fast at settings
    construction). Carries no values — just the fact.
    """

    def __init__(self) -> None:
        self.detail = (
            "Attachment storage is not configured "
            "(AWS_ENDPOINT_URL_S3 / BUCKET_NAME / AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY)"
        )
        super().__init__(self.detail)


# Lazy module-level singleton, mirroring app.database's engine pattern:
# constructed on first use so tests can monkeypatch ``boto3.client`` or
# settings before any client exists. ``reset_client_cache`` is the
# test/reload hook (analogous to ``get_settings.cache_clear()``).
# The lock guards first construction: async-def callers can't interleave
# inside the builder, but a future sync-def endpoint (threadpool) could —
# and ``boto3.client`` lazily initialises boto3's module-global default
# session, which boto3 documents as NOT thread-safe.
_client = None
_client_lock = threading.Lock()


def reset_client_cache() -> None:
    """Discard the cached S3 client (tests / settings reload)."""
    global _client
    with _client_lock:
        _client = None


def _get_client():
    """Return the process-wide S3 client, creating it on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # re-check under the lock
                settings = get_settings()
                if not settings.storage_is_configured:
                    raise StorageNotConfigured()
                _client = boto3.client(
                    "s3",
                    endpoint_url=settings.storage_endpoint_url,
                    # Tigris ignores region semantics and documents
                    # "auto"; pinning it keeps the SigV4 credential
                    # scope deterministic instead of inheriting
                    # AWS_REGION / ~/.aws/config from the host (a
                    # non-Fly deploy without AWS_REGION would otherwise
                    # silently sign for us-east-1).
                    region_name="auto",
                    aws_access_key_id=settings.storage_access_key_id,
                    aws_secret_access_key=settings.storage_secret_access_key,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "virtual"},
                    ),
                )
    return _client


# Characters allowed to survive in the filename portion of a storage
# key. Everything else (path separators, CJK, spaces, control chars)
# collapses to "-": object keys must be predictable ASCII, and the
# original filename is cosmetic — identity comes from the UUID prefix.
_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_LEN = 80


def build_storage_key(
    job_id: uuid.UUID, timeline_item_id: uuid.UUID, filename: str
) -> str:
    """Build a collision-free, traversal-safe object key.

    Shape: ``jobs/{job_id}/timeline/{item_id}/{uuid4hex}-{safe_name}``.
    The UUID prefix guarantees uniqueness (two uploads of ``photo.jpg``
    to the same item never collide); sanitisation guarantees the
    caller-supplied filename cannot inject path segments (``../``) or
    non-ASCII into the key. Fits comfortably in the ``VARCHAR(512)``
    ``storage_key`` column: fixed parts ~110 chars + capped filename.
    """
    # Basename only — strip any path the client (or an attacker) sent.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe = _SAFE_FILENAME_CHARS.sub("-", basename).strip("-.")
    if not safe:
        safe = "file"
    safe = safe[:_MAX_FILENAME_LEN]
    return (
        f"jobs/{job_id}/timeline/{timeline_item_id}/{uuid.uuid4().hex}-{safe}"
    )


def generate_presigned_put(storage_key: str, content_type: str) -> str:
    """Presigned PUT URL for a direct client upload.

    ``ContentType`` is part of the signature: the client must send the
    exact ``Content-Type`` header it declared to the issue endpoint, so
    a URL requested for a JPEG cannot be used to upload arbitrary
    content types.

    CALLER CONTRACT: this layer signs whatever ``content_type`` it is
    given — allow-listing is NOT done here. Callers must pass only
    schema-validated values (PR 3's ``AttachmentUploadRequest`` pins
    ``image/jpeg`` / ``image/png``); signing e.g. ``text/html`` would
    let an uploaded payload render as a page on the bucket origin.

    Known limit of SigV4 *query* auth: object SIZE is not bindable —
    ``Content-Length`` is absent at presign time (``content-length-range``
    exists only for presigned POST), so the URL holder may upload any
    byte size (and re-PUT the same key) until expiry. PR 7's confirm
    step and object-storage monitoring are the size controls; the
    storage layer cannot bound it.
    """
    settings = get_settings()
    return _get_client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.storage_bucket_name,
            "Key": storage_key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.storage_presign_expiry_seconds,
    )


def generate_presigned_get(storage_key: str) -> str:
    """Presigned GET URL for a short-lived direct download."""
    settings = get_settings()
    return _get_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.storage_bucket_name,
            "Key": storage_key,
        },
        ExpiresIn=settings.storage_presign_expiry_seconds,
    )
