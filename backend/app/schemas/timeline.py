"""Public-facing Job Timeline schemas (PR 3 — schemas only).

Shapes for the future ``/jobs/{job_id}/timeline`` + ``/timeline/*`` +
``/jobs/{job_id}/checklist`` routes (routers/services land in later
PRs). Inbound bodies: :class:`TimelineItemCreate`,
:class:`TimelineItemUpdate`, :class:`IssueStatusUpdate`,
:class:`AttachmentUploadRequest`, :class:`AttachmentConfirm`,
:class:`ChecklistToggle`. Outbound: :class:`TimelineItemPublic`
(+ :class:`TimelineItemDetailPublic` with nested attachments),
:class:`AttachmentPublic`, :class:`ChecklistItemPublic`,
:class:`AttachmentUploadResponse`, :class:`TimelineItemListResponse`.

Design notes
------------
* Enums (``TimelineItemType`` / ``IssueStatus`` / ``IssueSeverity`` /
  ``AttachmentUploadStatus``) are imported from :mod:`app.models` —
  never re-declared as strings.
* :class:`TimelineItemCreate` enforces the issue contract at the edge:
  ``item_type='issue'`` requires a ``title`` and defaults ``status`` to
  ``open``; any non-issue type carrying a ``status`` is rejected (422),
  mirroring the DB CHECK ``ck_timeline_items_issue_requires_status``
  so bad payloads fail before reaching the database.
* :class:`TimelineItemUpdate` is all-optional and consumed by the
  service layer with ``exclude_unset`` (conditional-spread contract):
  an omitted field is untouched; an explicit ``null`` clears a nullable
  field (e.g. unlink ``checklist_item_id``). ``item_type`` and
  ``status`` are deliberately NOT updatable here — records don't change
  kind after capture, and issue status flows through the dedicated
  ``PATCH /timeline/{id}/status`` transition endpoint so the
  open→resolved→closed state machine (closed = admin-only) cannot be
  bypassed by a generic edit.
* ``job_id`` / ``created_by`` never appear in inbound bodies: job comes
  from the URL path, actor from the auth token.
* :class:`AttachmentUploadRequest` whitelists ``content_type`` to
  JPEG/PNG (the mobile capture pipeline emits JPEG; PNG for screenshots)
  and carries the evidence metadata (``taken_at`` / GPS) read from EXIF
  *before* the client-side resize strips it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    AttachmentUploadStatus,
    IssueSeverity,
    IssueStatus,
    TimelineItemType,
)


class TimelineItemCreate(BaseModel):
    """Body of ``POST /jobs/{job_id}/timeline``.

    ``severity`` is accepted for any type (Phase 2 reserved column; the
    DB permits it on all rows) but ``status`` is issue-only — the
    validator below mirrors the DB CHECK plus the product rule that a
    new issue starts ``open`` unless explicitly created ``resolved``.
    """

    item_type: TimelineItemType
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = None
    status: IssueStatus | None = None
    severity: IssueSeverity | None = None
    checklist_item_id: uuid.UUID | None = None
    # Phase 2 reserved: recorded but drives no notification/filtering yet.
    assigned_user_id: uuid.UUID | None = None
    # Phase 2 reserved: no enforcement in MVP.
    requires_evidence: bool = False
    # Event time (backfillable); the timeline sort key.
    occurred_at: datetime

    @model_validator(mode="after")
    def _issue_contract(self) -> "TimelineItemCreate":
        if self.item_type is TimelineItemType.issue:
            if self.title is None:
                raise ValueError("an issue requires a title")
            if self.status is None:
                self.status = IssueStatus.open
        elif self.status is not None:
            raise ValueError(
                "status is only valid for item_type='issue'"
            )
        return self


class TimelineItemUpdate(BaseModel):
    """Body of ``PATCH /timeline/{item_id}``. Every field optional.

    Consumed with ``model_dump(exclude_unset=True)`` in the service
    layer, so omitted fields are untouched and an explicit ``null``
    clears a nullable column. ``item_type``/``status`` are excluded by
    design (see module docstring).
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = None
    severity: IssueSeverity | None = None
    checklist_item_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    requires_evidence: bool | None = None
    occurred_at: datetime | None = None


class IssueStatusUpdate(BaseModel):
    """Body of ``PATCH /timeline/{item_id}/status`` (issues only).

    Target state only; transition legality (contributor: open↔resolved,
    admin: + closed) is enforced in the service layer where the current
    state and caller role are known.
    """

    status: IssueStatus


class AttachmentUploadRequest(BaseModel):
    """Body of ``POST /timeline/{item_id}/attachments`` — presigned-URL issue.

    Evidence metadata (``taken_at``, GPS) is read from EXIF client-side
    *before* resize (which strips it) and stored as first-class columns.
    GPS is optional and never blocks capture (iOS camera returns no GPS
    tags; Android 10+ gallery GPS is unreliable).
    """

    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["image/jpeg", "image/png"]
    taken_at: datetime | None = None
    gps_lat: float | None = Field(default=None, ge=-90, le=90)
    gps_lng: float | None = Field(default=None, ge=-180, le=180)


class AttachmentUploadResponse(BaseModel):
    """201 body: short-lived presigned PUT URL + the pending row's identity."""

    attachment_id: uuid.UUID
    storage_key: str
    presigned_url: str


class AttachmentConfirm(BaseModel):
    """Body of ``POST /timeline/attachments/{attachment_id}/confirm``.

    Flips ``upload_status`` pending→confirmed and records the final
    object dimensions. ``byte_size`` is required (a zero-byte upload is
    a failed upload); width/height stay optional to match the nullable
    columns.
    """

    byte_size: int = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class AttachmentPublic(BaseModel):
    """Serialised view of a :class:`~app.models.timeline.TimelineAttachment`.

    ``download_url`` is a short-lived presigned GET URL populated by the
    service on ``GET /timeline/attachments/{id}`` only — list/detail
    responses may leave it ``None`` (clients request a fresh URL per
    download; the value is never persisted).
    """

    model_config = ConfigDict(from_attributes=True)

    attachment_id: uuid.UUID
    timeline_item_id: uuid.UUID
    storage_key: str
    content_type: str
    byte_size: int | None
    width: int | None
    height: int | None
    taken_at: datetime | None
    gps_lat: float | None
    gps_lng: float | None
    upload_status: AttachmentUploadStatus
    created_by: uuid.UUID
    created_at: datetime
    download_url: str | None = None


class TimelineItemPublic(BaseModel):
    """Compact timeline-item wire shape for list + post-create + post-patch."""

    model_config = ConfigDict(from_attributes=True)

    timeline_item_id: uuid.UUID
    job_id: uuid.UUID
    item_type: TimelineItemType
    title: str | None
    body: str | None
    status: IssueStatus | None
    severity: IssueSeverity | None
    checklist_item_id: uuid.UUID | None
    assigned_user_id: uuid.UUID | None
    requires_evidence: bool
    occurred_at: datetime
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class TimelineItemDetailPublic(TimelineItemPublic):
    """Body of ``GET /timeline/{item_id}`` — adds nested attachments.

    Mirrors the ``ExpensePublic`` / ``ExpenseDetailPublic`` split: list
    responses stay compact; the detail endpoint eager-loads attachments
    (``selectinload`` — this codebase has no async lazy loading) and
    returns them inline.
    """

    attachments: list[AttachmentPublic] = []


class TimelineItemListResponse(BaseModel):
    """Body of ``GET /jobs/{job_id}/timeline``. Cursor-style pagination."""

    items: list[TimelineItemPublic]
    next_cursor: str | None = None


class ChecklistItemPublic(BaseModel):
    """Serialised view of a :class:`~app.models.timeline.JobChecklistItem`."""

    model_config = ConfigDict(from_attributes=True)

    checklist_item_id: uuid.UUID
    job_id: uuid.UUID
    label: str
    phase: str | None
    sort_order: int
    is_done: bool
    done_at: datetime | None
    done_by: uuid.UUID | None
    requires_evidence: bool
    created_at: datetime


class ChecklistToggle(BaseModel):
    """Body of ``PATCH /jobs/{job_id}/checklist/{checklist_item_id}/toggle``.

    Explicit target state (not a blind flip) so retries under weak
    network are idempotent. ``done_at``/``done_by`` are stamped by the
    service, never by the caller.
    """

    is_done: bool
