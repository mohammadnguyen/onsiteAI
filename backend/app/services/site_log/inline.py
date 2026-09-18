"""Inline-text identity and the expectation revision 1 sets.

Shared by the declare path, which writes the inline row, and the upload
path, which reserves it and verifies what was stored against it. It holds no
orchestration and imports nothing from the clusters that use it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site_log import SiteLogEvent, SiteLogEventRevision

from .core import INLINE_TEXT_NAMESPACE


@dataclass(frozen=True)
class InlineExpectation:
    """Revision 1's text, and the receipt an honest upload of it produces."""

    body_text: str
    sha256: str
    size_bytes: int


async def _inline_expectation(
    db: AsyncSession, event: SiteLogEvent
) -> InlineExpectation | None:
    """What the inline object must contain, read from revision 1 itself.

    Revision 1 is the as-captured original and is the ONLY source of inline
    bytes. The request body is never used — not on the first attempt and
    not on a retry — so re-sending different text cannot change what the
    event is recorded as having said.

    Returns None when revision 1 is absent or carries no text. Callers
    refuse; none of them falls back to a caller-supplied value.
    """
    q = select(SiteLogEventRevision).where(
        SiteLogEventRevision.site_log_event_id == event.site_log_event_id,
        SiteLogEventRevision.tenant_id == event.tenant_id,
        SiteLogEventRevision.revision_no == 1,
    )
    revision = (await db.execute(q)).scalar_one_or_none()
    if revision is None or revision.body_text is None:
        return None
    raw = revision.body_text.encode("utf-8")  # exact bytes, no normalisation
    return InlineExpectation(
        body_text=revision.body_text,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )


def inline_attachment_id(capture_client_id: uuid.UUID) -> uuid.UUID:
    """Server-derived identity of the inline-text manifest row."""
    return uuid.uuid5(INLINE_TEXT_NAMESPACE, str(capture_client_id))


def declaration_fingerprint(
    *,
    body_text: str | None,
    internal_location: str | None,
    occurred_at: datetime | None,
    job_id: uuid.UUID | None,
    attachments: list[dict],
) -> str:
    """Canonical hash of every creation-time semantic field (2.1 §3).

    Idempotency-key-misuse detection only — never capture identity, never
    deduplication. The inline-text row is covered by ``body_text``, so
    ``attachments`` carries client-declared entries only.
    """
    canonical = {
        "attachments": sorted(
            (
                {
                    "attachment_client_id": str(a["attachment_client_id"]),
                    "declared_media_type": a["declared_media_type"],
                    "declared_size_bytes": a.get("declared_size_bytes"),
                }
                for a in attachments
            ),
            key=lambda a: a["attachment_client_id"],
        ),
        "body_text": body_text,
        "internal_location": internal_location,
        "job_id": str(job_id) if job_id else None,
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
