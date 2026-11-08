"""Pydantic schemas for the Potential Variation API (DEC-PV-001).

No response carries an amount, approver, signature or approval field, and
none carries the proposed title (it exists only in the audit, which no
endpoint returns). ``title`` is null, and the source block empty, when the
caller may not read the source entry; ``decision_note`` is null for anyone
but the builder. Tests pin the exact key sets.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus
from app.models.potential_variation import PvEndReason, PvOrigin, PvReviewState
from app.models.site_log import AttachmentState


class PvCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    create_client_id: uuid.UUID
    # Trimmed and length-checked by the service, which answers 422 with a
    # plain message; the bound here only stops absurd payloads early.
    title: str = Field(max_length=2000)
    # The entry version the person saw and confirmed.
    source_revision_no: int = Field(ge=1)
    # The job the person saw on the entry and confirmed; null when the
    # entry showed no job. Required either way: a request without it is
    # refused (422), so "no job" is never assumed.
    job_id: uuid.UUID | None


class PvDecisionIn(BaseModel):
    """One decision: the state, the end reason and the note together."""

    model_config = ConfigDict(extra="forbid")

    review_state: PvReviewState
    ended_reason: PvEndReason | None = None
    note: str | None = Field(default=None, max_length=2000)


class PvPatchIn(BaseModel):
    """Only what is sent is changed (``model_fields_set`` for ``job_id``).

    ``decision`` is the builder's; ``job_id`` may be set or changed but never
    cleared. Changing a job that is already set needs ``reason`` (the
    task API's field and limit), kept in the audit.
    """

    model_config = ConfigDict(extra="forbid")

    decision: PvDecisionIn | None = None
    job_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class PvUserOut(BaseModel):
    user_id: uuid.UUID
    full_name: str


class PvJobOut(BaseModel):
    """Job identity only — no money."""

    job_id: uuid.UUID
    job_name: str
    job_code: str | None
    status: JobStatus


class PvSourceOut(BaseModel):
    site_log_event_id: uuid.UUID
    # The version the person confirmed when flagging.
    revision_no: int
    # False: the caller may not read the entry; the app says so and shows
    # nothing taken from it.
    readable: bool
    # The entry's current job — only when ``readable``, else null.
    job_id: uuid.UUID | None


class PvPinnedRevisionOut(BaseModel):
    revision_no: int
    body_text: str | None
    internal_location: str | None
    occurred_at: datetime | None
    # When that version was recorded.
    created_at: datetime


class PvAttachmentOut(BaseModel):
    """One of the entry's attachments as it is NOW (never a snapshot of the
    pinned version). Opened through the entry, under the entry's rule."""

    declared_media_type: str
    state: AttachmentState


class PvSourceDetailOut(PvSourceOut):
    """Detail-only additions; all null unless ``readable``."""

    pinned: PvPinnedRevisionOut | None
    current_revision_no: int | None
    current_withdrawn: bool | None
    current_attachments: list[PvAttachmentOut] | None


class PvPermissionsOut(BaseModel):
    decide: bool
    read_note: bool
    set_job: bool
    change_job: bool


class PvOut(BaseModel):
    potential_variation_id: uuid.UUID
    # Null when the caller may not read the source entry.
    title: str | None
    review_state: PvReviewState
    ended_reason: PvEndReason | None
    # The builder's note; null for everyone else.
    decision_note: str | None
    origin: PvOrigin
    job: PvJobOut | None
    created_by: PvUserOut | None
    decided_by: PvUserOut | None
    decided_at: datetime | None
    source: PvSourceOut
    permissions: PvPermissionsOut
    created_at: datetime
    updated_at: datetime


class PvDetailOut(PvOut):
    source: PvSourceDetailOut
