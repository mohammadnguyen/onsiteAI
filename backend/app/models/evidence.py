"""Raw-evidence layer of the capture spine (Evidence → Candidate →
Confirmation → Truth), per DEC-EVIDENCE-001 / DEC-TIME-001.

Design constraints baked into this schema:

* Raw evidence is never destroyed. There is no delete column and no
  delete code path in this slice; the storage interface itself exposes
  no delete/overwrite. A future privileged purge (legal/compliance)
  is a separate promoted decision with its own restricted interface —
  deliberately not designed here.
* ``occurred_at`` (when the fact happened on site) and the
  ``TimestampMixin`` ``created_at`` (when the record was written) are
  distinct and both always stored (DEC-TIME-001).
* ``job_id`` is CONFIRMED-ONLY: populated exclusively by explicit user
  action — explicit selection at upload or a later ``link-job`` action
  (DEC-JOB-ATTR-001). Suggestion/attribution state belongs to the
  future capture slice, not this table; there is deliberately no
  ``job_attribution_status`` here.
* This table carries no candidate/extraction columns — it is not a
  Truth table and cannot represent a confirmed fact (DEC-TRUTH-001).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy import (
    Enum as SqlaEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.job import Job
from app.models.user import User


class EvidenceMediaType(str, enum.Enum):
    """Coarse media class, derived from the upload's MIME type."""

    audio = "audio"
    image = "image"
    text = "text"
    document = "document"


class EvidenceStatus(str, enum.Enum):
    """Upload lifecycle.

    ``pending``  — row created, bytes not yet verified in storage.
    ``stored``   — bytes verified (size + sha256) in the object store.
    ``failed``   — upload aborted (size cap, storage error, disconnect).

    Abandoned ``pending`` rows (process death mid-stream) are found by
    the manual sweep query documented in the evidence service — no
    automated sweep exists in this slice.
    """

    pending = "pending"
    stored = "stored"
    failed = "failed"


class Evidence(Base, TimestampMixin):
    """One raw evidence object (voice memo, photo, text note, document)."""

    __tablename__ = "evidence"

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # CONFIRMED-ONLY (see module docstring). SET NULL so evidence
    # survives the hard-delete-empty-job action — evidence outlives
    # everything (DEC-EVIDENCE-001).
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Default NO ACTION: a user with evidence can never be hard-deleted.
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )

    media_type: Mapped[EvidenceMediaType] = mapped_column(
        SqlaEnum(
            EvidenceMediaType,
            name="evidence_media_type",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    status: Mapped[EvidenceStatus] = mapped_column(
        SqlaEnum(
            EvidenceStatus,
            name="evidence_status",
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=EvidenceStatus.pending,
    )

    # Set when the adapter confirms the bytes (status=stored); NULL while
    # pending/failed.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Immutable unique object key (evidence_id + content-hash prefix),
    # written once when the upload completes. ``storage_backend`` is
    # physical locator metadata only (which adapter/root holds the
    # object) — it carries no business semantics.
    storage_backend: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    storage_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )

    # When the evidence was captured on site — distinct from created_at
    # (DEC-TIME-001). A photo uploaded at night about a morning event
    # keeps its morning occurred_at.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    uploaded_by: Mapped["User"] = relationship(
        lazy="joined", foreign_keys=[uploaded_by_user_id]
    )
    job: Mapped["Job | None"] = relationship(
        lazy="select", foreign_keys=[job_id]
    )

    __table_args__ = (
        Index("ix_evidence_job_created", "job_id", "created_at"),
        Index("ix_evidence_uploader_created", "uploaded_by_user_id", "created_at"),
        Index("ix_evidence_sha256", "sha256"),
        Index("ix_evidence_status", "status"),
    )


class EvidenceAuditLog(Base):
    """Append-only audit row for an evidence lifecycle event.

    Follows the :class:`app.models.job_audit_log.JobAuditLog` precedent:
    single-tenant server_default ``tenant_id``, ``clock_timestamp()``
    created_at (distinct real-time values within one transaction), and
    an FK cascade that can never orphan or lose audit rows.
    """

    __tablename__ = "evidence_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=text("'00000000-0000-0000-0000-000000000001'::uuid"),
    )

    # Evidence rows are never deleted, so a plain NOT NULL FK (default
    # NO ACTION) is safe and guarantees the trail stays attached.
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence.evidence_id"),
        nullable=False,
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )

    # "uploaded" | "stored" | "failed" | "job_linked" — application-validated.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    actor: Mapped["User"] = relationship(
        lazy="joined", foreign_keys=[actor_user_id]
    )

    __table_args__ = (
        Index(
            "ix_evidence_audit_log_tenant_evidence_created",
            "tenant_id",
            "evidence_id",
            "created_at",
        ),
    )
