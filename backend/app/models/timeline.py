"""Job Timeline models (PR 1 — data layer only).

The Timeline module adds the third pillar of SiteTracker's operating
model — *site facts* — alongside the existing Expenses (money) and
Labour (people/time) pillars. This module defines the four backing
tables and their enums. No service, API router, or Pydantic schema is
introduced here; those land in later PRs.

Design notes (see the module plan for the full rationale):

* **Single-table timeline.** :class:`TimelineItem` uses an
  ``item_type`` discriminator instead of one table per record kind.
  MVP exposes ``daily_note`` / ``photo`` / ``issue``; the remaining
  enum values (``delay`` / ``variation`` / ``inspection`` /
  ``completion``) are reserved for Phase 2 so new record kinds are an
  enum addition, not a new table.
* **Issue-only columns are nullable.** ``status`` / ``severity`` apply
  only to ``item_type='issue'`` rows. A DB ``CHECK`` enforces that an
  ``issue`` always carries a ``status`` (non-issue rows leave it NULL).
* **UUID keys, matching the codebase.** Every existing table keys on a
  ``UUID`` ``<entity>_id`` column and FKs reference ``jobs.job_id`` /
  ``users.user_id``. These models follow that convention exactly — a
  ``BIGINT`` key could not reference the existing UUID primary keys.
* **Soft-delete.** ``timeline_items`` / ``timeline_attachments`` /
  ``job_checklist_items`` inherit :class:`~app.models.mixins.
  SoftDeleteMixin` (nullable ``deleted_at``); a global query filter
  (registered in :mod:`app.database`) excludes soft-deleted rows by
  default, and partial indexes (``WHERE deleted_at IS NULL``) keep
  active-row queries fast. The append-only audit log has no
  ``deleted_at`` and does not use the mixin.
* **Audit idiom reused.** :class:`TimelineAuditLog` mirrors
  :class:`~app.models.job_audit_log.JobAuditLog`: ``clock_timestamp()``
  default so rapid inserts inside one transaction keep a distinct,
  DESC-orderable ``created_at``; and ``timeline_item_id`` is a plain
  column (no hard FK) so audit history still points at an item after
  it is soft-deleted.

Purely additive: every FK targets an existing table; no existing
column is modified. The model is registered in
:mod:`app.models.__init__` and :mod:`alembic.env` so both
``Base.metadata.create_all`` (test bootstrap) and Alembic autogenerate
see it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SqlaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.mixins import SoftDeleteMixin


class TimelineItemType(str, enum.Enum):
    """Kind of timeline record.

    ``daily_note`` / ``photo`` / ``issue`` are the MVP-exposed types;
    the remaining values are reserved for Phase 2 (UI does not surface
    them yet) so adding a record kind is an enum change, not a new
    table. Declaration order is not load-bearing.
    """

    daily_note = "daily_note"
    photo = "photo"
    issue = "issue"
    # --- Phase 2 reserved (schema only; not exposed by the MVP UI) ---
    delay = "delay"
    variation = "variation"
    inspection = "inspection"
    completion = "completion"


class IssueStatus(str, enum.Enum):
    """Lifecycle of an ``issue`` timeline item.

    ``open`` (raised) -> ``resolved`` (field claims fixed) -> ``closed``
    (admin verification). The ``resolved`` vs ``closed`` split mirrors
    the industry "two-stage sign-off" convention; the closed transition
    is admin-only, enforced in the service layer in a later PR.
    """

    open = "open"
    resolved = "resolved"
    closed = "closed"


class IssueSeverity(str, enum.Enum):
    """Severity of an ``issue`` timeline item (Phase 2 use; reserved)."""

    low = "low"
    medium = "medium"
    high = "high"


class AttachmentUploadStatus(str, enum.Enum):
    """Two-phase direct-upload state for a :class:`TimelineAttachment`.

    A row is inserted ``pending`` when the presigned PUT URL is issued
    and flipped to ``confirmed`` once the client confirms the upload.
    Orphaned ``pending`` rows are reaped by a background task (later PR).
    """

    pending = "pending"
    confirmed = "confirmed"


class JobChecklistItem(Base, SoftDeleteMixin):
    """A pre-set, per-job checklist entry (deliberately minimal).

    Static definition, distinct from the dynamic timeline event stream,
    so it lives in its own table rather than in ``timeline_items``. The
    ``phase`` column is reserved for the Phase 2 template engine; the
    MVP only uses pre-set flat items. Created before
    :class:`TimelineItem` because the latter FKs to this table.
    """

    __tablename__ = "job_checklist_items"

    checklist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # Phase 2 template engine (project type -> phase -> items); reserved.
    phase: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    done_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    # Phase 2 "completion needs photo evidence" enforcement; reserved.
    requires_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # deleted_at supplied by SoftDeleteMixin.

    __table_args__ = (
        # Full index on the FK column for job-scoped lookups that must
        # include soft-deleted rows (admin / restore paths). Active-row
        # queries use the partial index below.
        Index("ix_job_checklist_items_job", "job_id"),
        Index(
            "ix_job_checklist_items_active",
            "job_id",
            "sort_order",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class TimelineItem(Base, TimestampMixin, SoftDeleteMixin):
    """A single site-fact record on a job's timeline.

    One table, discriminated by ``item_type``. Issue-only columns
    (``status`` / ``severity``) are NULL for non-issue rows; a CHECK
    guarantees an ``issue`` always has a ``status``.
    """

    __tablename__ = "timeline_items"

    timeline_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False
    )
    item_type: Mapped[TimelineItemType] = mapped_column(
        SqlaEnum(
            TimelineItemType,
            name="timeline_item_type",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- issue-only (NULL for non-issue rows) ---
    status: Mapped[IssueStatus | None] = mapped_column(
        SqlaEnum(
            IssueStatus,
            name="issue_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=True,
    )
    severity: Mapped[IssueSeverity | None] = mapped_column(
        SqlaEnum(
            IssueSeverity,
            name="issue_severity",
            native_enum=True,
            create_type=True,
        ),
        nullable=True,
    )
    # --- associations ---
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_checklist_items.checklist_item_id"),
        nullable=True,
    )
    # Phase 2 assignment target; reserved (no notifications in MVP).
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    # Phase 2 evidence enforcement; reserved.
    requires_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # --- time + attribution ---
    # Event time (may be back-filled); the timeline sort key.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    # created_at / updated_at supplied by TimestampMixin;
    # deleted_at supplied by SoftDeleteMixin.

    # One-way navigation to attachments, scoped to this module so no
    # existing model (Job / User) is edited. Richer relationships arrive
    # with the service layer.
    attachments: Mapped[list["TimelineAttachment"]] = relationship(
        back_populates="timeline_item",
        lazy="select",
    )

    __table_args__ = (
        # An issue must always carry a status; non-issue rows leave it
        # NULL. Written as ``NOT issue OR status present`` so it holds
        # for every other item_type.
        CheckConstraint(
            "item_type != 'issue' OR status IS NOT NULL",
            name="ck_timeline_items_issue_requires_status",
        ),
        # Primary timeline read: newest-first per job.
        Index("ix_timeline_items_job_occurred", "job_id", text("occurred_at DESC")),
        # Filter-by-type per job.
        Index("ix_timeline_items_job_type", "job_id", "item_type"),
        # Active-row timeline read (excludes soft-deleted).
        Index(
            "ix_timeline_items_active",
            "job_id",
            text("occurred_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # checklist_item_id FK lookups (no composite covers this column).
        Index("ix_timeline_items_checklist_item", "checklist_item_id"),
    )


class TimelineAttachment(Base, SoftDeleteMixin):
    """A photo / file attached to a :class:`TimelineItem`.

    ``storage_key`` points at the object-storage object (Tigris, later
    PR). The evidence-metadata columns (``taken_at`` / ``gps_lat`` /
    ``gps_lng``) are stored as first-class application data because the
    resize/compress step on the client strips most EXIF from the file
    itself.
    """

    __tablename__ = "timeline_attachments"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timeline_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("timeline_items.timeline_item_id"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # --- evidence metadata (timestamp + GPS + attribution) ---
    taken_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gps_lat: Mapped[float | None] = mapped_column(Double, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Double, nullable=True)
    upload_status: Mapped[AttachmentUploadStatus] = mapped_column(
        SqlaEnum(
            AttachmentUploadStatus,
            name="attachment_upload_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=AttachmentUploadStatus.pending,
        server_default=AttachmentUploadStatus.pending.value,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # deleted_at supplied by SoftDeleteMixin.

    timeline_item: Mapped["TimelineItem"] = relationship(
        back_populates="attachments"
    )

    __table_args__ = (
        # Full FK index (includes soft-deleted rows).
        Index("ix_timeline_attachments_item", "timeline_item_id"),
        # Active-row lookups per item.
        Index(
            "ix_timeline_attachments_active",
            "timeline_item_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class TimelineAuditLog(Base):
    """Append-only audit row for a timeline item change.

    Mirrors :class:`~app.models.job_audit_log.JobAuditLog`:

    * ``timeline_item_id`` is a plain column with **no** hard FK, so the
      audit trail still points at an item after it is soft-deleted (and
      would survive a future hard delete).
    * ``created_at`` defaults to ``clock_timestamp()`` (not ``now()``)
      so several audit rows written in one transaction get distinct,
      DESC-orderable timestamps.

    ``action`` is application-validated (``create`` / ``update`` /
    ``soft_delete`` / ``status_change``); no DB CHECK, so new actions do
    not need a follow-up migration.
    """

    __tablename__ = "timeline_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Intentionally NOT a hard FK: the audit row must remain valid after
    # its timeline item is soft-deleted.
    timeline_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False
    )
    # "create" | "update" | "soft_delete" | "status_change" —
    # application-validated; no DB CHECK (see class docstring).
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    # Change snapshot / field diff; shape left to the service layer.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ``clock_timestamp()`` (NOT ``now()``) for distinct per-insert
    # timestamps within one transaction — see class docstring.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_timeline_audit_log_item", "timeline_item_id"),
        Index("ix_timeline_audit_log_job_created", "job_id", text("created_at DESC")),
    )
