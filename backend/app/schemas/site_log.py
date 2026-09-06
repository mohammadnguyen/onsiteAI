"""Pydantic schemas for the Site Log capture API (WP A A2a).

Contributor-facing response models deliberately carry NO eligibility
field (founder ruling D5): eligibility governance is not part of any
ordinary response shape and cannot accrete here by reuse. The response
tests pin the exact key set.

``job_state`` is a computed field derived from ``job_id`` nullability —
the database stores no such column (Revision 2 §5.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.site_log import AttachmentState, CaptureStatus

MediaType = Literal["audio", "image", "text", "document"]


class AttachmentDeclareIn(BaseModel):
    attachment_client_id: uuid.UUID
    declared_media_type: MediaType
    declared_size_bytes: int | None = Field(default=None, ge=0)


class CaptureDeclareIn(BaseModel):
    capture_client_id: uuid.UUID
    job_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    internal_location: str | None = Field(default=None, max_length=255)
    body_text: str | None = None
    attachments: list[AttachmentDeclareIn] = Field(default_factory=list)

    @field_validator("body_text")
    @classmethod
    def _text_not_blank(cls, v: str | None) -> str | None:
        # Exact bytes are preserved downstream; only the all-whitespace
        # case is rejected (2.1 §3). No stripping, no normalisation.
        if v is not None and v.strip() == "":
            raise ValueError("body_text must contain non-whitespace text")
        return v


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision_no: int
    body_text: str | None
    internal_location: str | None
    occurred_at: datetime | None
    withdrawn: bool
    created_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attachment_client_id: uuid.UUID
    declared_media_type: str
    declared_size_bytes: int | None
    state: AttachmentState
    evidence_id: uuid.UUID | None


class SiteLogEventOut(BaseModel):
    """Contributor shape. No eligibility, no attempt counters."""

    site_log_event_id: uuid.UUID
    author_user_id: uuid.UUID
    job_id: uuid.UUID | None
    job_state: Literal["confirmed", "unassigned"]
    capture_status: CaptureStatus
    created_at: datetime
    revision: RevisionOut
    attachments: list[AttachmentOut]


class AssignJobIn(BaseModel):
    job_id: uuid.UUID


class RelinkJobIn(BaseModel):
    job_id: uuid.UUID
    reason: str | None = None


class ResetAttachmentIn(BaseModel):
    reason: str | None = None


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attachment_client_id: uuid.UUID
    state: AttachmentState
    evidence_id: uuid.UUID | None
    sha256: str | None
    size_bytes: int | None
