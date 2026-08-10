"""Pydantic schemas for the evidence API.

Deliberately narrow: there is no suggestion/attribution field anywhere
in this surface — ``job_id`` is always an explicit user selection
(DEC-JOB-ATTR-001), and no schema can express a delete.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.evidence import EvidenceMediaType, EvidenceStatus


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: uuid.UUID
    job_id: uuid.UUID | None
    uploaded_by_user_id: uuid.UUID
    media_type: EvidenceMediaType
    mime_type: str
    original_filename: str | None
    status: EvidenceStatus
    size_bytes: int | None
    sha256: str | None
    # Nullable at the raw layer (DEC-TIME-001): unknown stays NULL,
    # never manufactured from upload time.
    occurred_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvidenceLinkJobIn(BaseModel):
    """Explicit user action: link evidence to its authoritative Job.

    ``reason`` is required (non-empty) only for a relink of an
    already-linked record — admin-only, audited as ``job_relinked``.
    """

    job_id: uuid.UUID
    reason: str | None = None
