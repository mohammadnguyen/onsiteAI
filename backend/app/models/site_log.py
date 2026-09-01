"""Site Log capture layer — WP A (Slice 1 Work Package A), schema only.

Five tables, all additive, per the approved Revision 3 plan plus founder
rulings O1/O2:

* ``site_log_events`` — one row per capture. Carries NO content column
  (content is immutable Raw Evidence plus append-only revisions), NO
  ``occurred_at`` (O2: revisions are the single source of truth) and NO
  ``job_state`` column (derived from ``job_id IS NULL``; the API reports
  it as a computed field).
* ``site_log_event_revisions`` — append-only display/correction history.
  Revision 1 is the as-captured version; corrections and withdrawal
  append, never overwrite. The original Evidence payload is never
  touched by any revision.
* ``site_log_event_attachments`` — the attachment manifest (O1). Declared
  at event creation with a device-generated ``attachment_client_id`` so
  uploads retry safely; ``evidence_id`` is REQUIRED to transition once,
  NULL→final — the database enforces exclusivity and stored⇒bound, and
  the once-only rule itself is a mandatory A2 service invariant (see
  the class docstring for the exact boundary); ``state`` is the only
  repeatedly mutable domain field and is an operational upload
  projection — not Raw Evidence, not eligibility, not Truth.
* ``site_log_event_audit_log`` — append-only who-did-what trail on the
  ``job_audit_log`` precedent. Content-free: raw text, audio/photo
  content and body snapshots never appear here (the revisions table is
  the content history).
* ``capture_eligibility_transitions`` — append-only group-scoped capture
  eligibility. Current state is the row with the highest
  ``transition_no`` (assigned monotonically inside the writing
  transaction with the event row locked); there is deliberately NO
  ``current_state`` column to drift. ``development_released`` is a
  transition *reason*, never a state.

Boundary notes (DEC-TRUTH-001 / DEC-AI-BOUNDARY-001): none of these
tables is a Truth table, none carries candidate or extraction columns,
and no model-facing code exists in WP A. Raw evidence remains immutable
and delete-free (DEC-EVIDENCE-001).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SqlaEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.evidence import Evidence
from app.models.job import Job
from app.models.user import User

# Single-tenant default, matching job_audit_log / evidence_audit_log.
# Every WP A table carries tenant_id and every UNIQUE is tenant-scoped
# (founder ruling O6): the columns exist so queries can filter on them
# now and real tenancy can arrive without a rewrite.
_TENANT_DEFAULT = text("'00000000-0000-0000-0000-000000000001'::uuid")


class CaptureStatus(str, enum.Enum):
    """Upload lifecycle of the capture as a whole (server-computed).

    ``pending_upload``  — event exists; not all declared attachments are
                          ``stored`` yet.
    ``complete``        — every manifest entry reached ``stored``.
    ``partial_failed``  — at least one manifest entry is ``failed``.

    The value is derived from manifest state at finalize time — never
    client-asserted.
    """

    pending_upload = "pending_upload"
    complete = "complete"
    partial_failed = "partial_failed"


class AttachmentState(str, enum.Enum):
    """Manifest entry upload lifecycle (closed transition graph).

    awaiting_upload → pending → stored | failed

    The graph is enforced in the WP A2 service layer; the closed value
    set is enforced here by the enum type itself.
    """

    awaiting_upload = "awaiting_upload"
    pending = "pending"
    stored = "stored"
    failed = "failed"


# Allowed manifest-state transitions (O1 requirement 4). The service
# layer (A2) is the enforcement point; tests assert the graph here so a
# later edit to it is a visible, reviewed change.
ATTACHMENT_STATE_TRANSITIONS: dict[AttachmentState, frozenset[AttachmentState]] = {
    AttachmentState.awaiting_upload: frozenset(
        {AttachmentState.pending, AttachmentState.failed}
    ),
    AttachmentState.pending: frozenset(
        {AttachmentState.stored, AttachmentState.failed}
    ),
    AttachmentState.stored: frozenset(),
    AttachmentState.failed: frozenset({AttachmentState.pending}),
}


class CaptureEligibilityState(str, enum.Enum):
    """Group-scoped capture eligibility states.

    WP A writes only ``eligibility_pending_unexposed`` (the N1 default,
    recorded as the first transition on creation) and the one-way
    demotion to ``development_only``. The later states exist as values
    so the enum never needs an ALTER for the evaluation checkpoints,
    but no WP A code path writes them.

    ``development_released`` is deliberately NOT here — release is a
    transition *reason* whose resulting state is ``development_only``.
    """

    eligibility_pending_unexposed = "eligibility_pending_unexposed"
    content_blind_selected = "content_blind_selected"
    evaluation_locked = "evaluation_locked"
    first_authorized_evaluation_exposure = "first_authorized_evaluation_exposure"
    evaluated = "evaluated"
    development_only = "development_only"


class SiteLogAuditAction(str, enum.Enum):
    """Application-validated audit action vocabulary (String column,
    matching the job_audit_log precedent — the enum is the single place
    the vocabulary lives)."""

    created = "created"
    attachment_declared = "attachment_declared"
    attachment_state_changed = "attachment_state_changed"
    finalized = "finalized"
    job_assigned = "job_assigned"
    job_relinked = "job_relinked"
    revised = "revised"
    withdrawn = "withdrawn"
    eligibility_transition = "eligibility_transition"


# Audit rows are content-free (O1 requirement 8): these keys can carry
# raw capture content and must never appear in ``changed_fields``.
# ``validate_audit_detail`` is the writer-side guard; model tests pin it.
FORBIDDEN_AUDIT_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "body_text",
        "body",
        "content",
        "payload",
        "raw",
        "raw_text",
        "text",
        "transcript",
        "utterance",
        "snapshot",
        "bytes",
        "data",
    }
)


def validate_audit_detail(changed_fields: dict) -> dict:
    """Reject content-bearing keys in an audit payload.

    Audit rows record who did what — revision ids, field NAMES, sizes,
    hashes and other non-content metadata. Content history lives in the
    revisions table. Raises ``ValueError`` on any forbidden key, at any
    nesting depth.
    """

    def _walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                lowered = str(key).lower()
                if lowered in FORBIDDEN_AUDIT_DETAIL_KEYS:
                    raise ValueError(
                        f"audit changed_fields must be content-free: "
                        f"forbidden key {key!r} at {path or '<root>'}"
                    )
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                _walk(value, f"{path}[{i}]")

    _walk(changed_fields, "")
    return changed_fields


class SiteLogEvent(Base, TimestampMixin):
    """One capture: job-attributable, human-reviewed, content-free row.

    ``created_at`` (TimestampMixin) is the immutable server capture
    timestamp. ``occurred_at`` lives ONLY in revisions (O2).
    """

    __tablename__ = "site_log_events"

    site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # CONFIRMED-ONLY (DEC-JOB-ATTR-001): written exclusively by explicit
    # user action. NULL means unassigned — job_state is DERIVED from
    # this nullability; there is deliberately no stored job_state column.
    # NO ACTION (the repo's default-FK form): a Job referenced by a
    # capture is not an empty Job, so hard-deleting it is REJECTED.
    # NULL must mean "no Job confirmed yet" — never "the confirmed Job
    # was later deleted"; silently nulling would destroy the relational
    # attribution even though audit rows retain the UUID. Empty Jobs
    # with no referencing captures remain deletable.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id"),
        nullable=True,
    )

    # Device-generated idempotency key (offline safety): replaying the
    # same capture_client_id returns the existing event, never a second
    # row. Uniqueness is tenant-scoped and per-author.
    capture_client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    capture_status: Mapped[CaptureStatus] = mapped_column(
        SqlaEnum(
            CaptureStatus,
            name="site_log_capture_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CaptureStatus.pending_upload,
        server_default=CaptureStatus.pending_upload.value,
    )

    author: Mapped["User"] = relationship(
        lazy="select", foreign_keys=[author_user_id]
    )
    job: Mapped["Job | None"] = relationship(
        lazy="select", foreign_keys=[job_id]
    )
    revisions: Mapped[list["SiteLogEventRevision"]] = relationship(
        lazy="select",
        order_by="SiteLogEventRevision.revision_no",
        back_populates="event",
    )
    attachments: Mapped[list["SiteLogEventAttachment"]] = relationship(
        lazy="select", back_populates="event"
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "author_user_id",
            "capture_client_id",
            name="uq_slog_event_capture_client",
        ),
        Index("ix_slog_event_job_created", "job_id", "created_at"),
        Index("ix_slog_event_author_created", "author_user_id", "created_at"),
        # Unassigned-inbox scan: job IS NULL filtered per tenant.
        Index(
            "ix_slog_event_unassigned",
            "tenant_id",
            "created_at",
            postgresql_where=text("job_id IS NULL"),
        ),
    )


class SiteLogEventRevision(Base):
    """Append-only display/correction/withdrawal history (O2, O4).

    Never updated, never deleted. Revision 1 is the as-captured version;
    each correction appends the next ``revision_no``. ``occurred_at``
    here is the ONLY home of that value. A withdrawal appends a row with
    ``withdrawn = true`` and a mandatory reason; read access to the
    event is unchanged by withdrawal (default feeds exclude it, the
    record itself stays visible to everyone who could read it before).
    """

    __tablename__ = "site_log_event_revisions"

    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    # No cascade: revisions are history and history outlives nothing —
    # events have no delete path at all in WP A.
    site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )

    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)

    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Internal site location, distinct from the Job address
    # (DEC-LOCATION-001).
    internal_location: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # When the fact happened on site (DEC-TIME-001). Nullable — unknown
    # stays NULL, never defaulted. Single-sourced here (O2): the events
    # table has no copy.
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    withdrawn: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Mandatory for any correction (revision_no > 1) and any withdrawal.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    event: Mapped["SiteLogEvent"] = relationship(
        lazy="select", back_populates="revisions"
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "revision_no",
            name="uq_slog_revision_no",
        ),
        CheckConstraint("revision_no >= 1", name="ck_slog_revision_no_ge_1"),
        CheckConstraint(
            "(NOT withdrawn) OR (reason IS NOT NULL)",
            name="ck_slog_revision_withdrawn_reason",
        ),
        CheckConstraint(
            "(revision_no = 1) OR (reason IS NOT NULL)",
            name="ck_slog_revision_correction_reason",
        ),
        # Latest-revision retrieval without a projection column (O2).
        Index("ix_slog_revision_event_no", "site_log_event_id", "revision_no"),
        Index("ix_slog_revision_occurred", "occurred_at"),
    )


class SiteLogEventAttachment(Base, TimestampMixin):
    """Attachment manifest entry (O1) — an operational upload projection.

    Immutable after declaration: identity, tenant, parent event,
    ``attachment_client_id`` and the declared media metadata never
    change. ``evidence_id`` is required to transition exactly once,
    NULL → final. **Enforcement boundary, stated precisely:** the
    database enforces (a) exclusivity — the partial UNIQUE keeps one
    Evidence on at most one attachment row — and (b) stored⇒bound — the
    CHECK forbids a ``stored`` row with a NULL ``evidence_id``. The
    once-only rule itself (never rebind a non-null ``evidence_id`` to a
    different Evidence; never clear it outside ``stored``) is NOT
    database-enforced: it is a MANDATORY A2 service invariant, on the
    repository precedent that write-path immutability lives in the
    service layer (``evidence.job_id`` has exactly two explicit writers
    and no DB guard). ``state`` is the only repeatedly mutable domain
    field (plus the TimestampMixin operational timestamps); its closed
    transition graph is ``ATTACHMENT_STATE_TRANSITIONS``. Rows are never
    deleted, never reassigned to another event, never reused by another
    capture — no code path for any of those exists, and ``evidence_id``
    uniqueness plus the tenant-scoped client-id uniqueness make reuse a
    constraint violation rather than a review catch.
    """

    __tablename__ = "site_log_event_attachments"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )

    # Device-generated per-attachment idempotency key: a retried upload
    # matches its manifest row instead of creating a duplicate Evidence.
    attachment_client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # Declared at manifest creation; immutable. String + CHECK rather
    # than the evidence enum so this table never couples to evidence's
    # enum lifecycle (the authoritative media_type lives on Evidence).
    declared_media_type: Mapped[str] = mapped_column(String(32), nullable=False)

    declared_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    # NULL until the upload lands; then set exactly once. Partial UNIQUE
    # below keeps attachment exclusive: one Evidence belongs to at most
    # one SiteLogEvent (Revision 3 correction 3).
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence.evidence_id"),
        nullable=True,
    )

    state: Mapped[AttachmentState] = mapped_column(
        SqlaEnum(
            AttachmentState,
            name="site_log_attachment_state",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=AttachmentState.awaiting_upload,
        server_default=AttachmentState.awaiting_upload.value,
    )

    event: Mapped["SiteLogEvent"] = relationship(
        lazy="select", back_populates="attachments"
    )
    evidence: Mapped["Evidence | None"] = relationship(
        lazy="select", foreign_keys=[evidence_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "attachment_client_id",
            name="uq_slog_attachment_client",
        ),
        Index(
            "uq_slog_attachment_evidence",
            "evidence_id",
            unique=True,
            postgresql_where=text("evidence_id IS NOT NULL"),
        ),
        CheckConstraint(
            "declared_media_type IN ('audio', 'image', 'text', 'document')",
            name="ck_slog_attachment_media_type",
        ),
        CheckConstraint(
            "declared_size_bytes IS NULL OR declared_size_bytes >= 0",
            name="ck_slog_attachment_size_nonneg",
        ),
        # A stored attachment must reference its Evidence row.
        CheckConstraint(
            "state != 'stored' OR evidence_id IS NOT NULL",
            name="ck_slog_attachment_stored_has_evidence",
        ),
        Index("ix_slog_attachment_event", "site_log_event_id"),
    )


class SiteLogEventAuditLog(Base):
    """Append-only who-did-what trail (job_audit_log precedent).

    Content-free by rule (O1 requirement 8): ``changed_fields`` records
    revision ids, field names, sizes, hashes and other non-content
    metadata — never body text, transcripts or file content. Writers go
    through :func:`validate_audit_detail`.
    """

    __tablename__ = "site_log_event_audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    # Events are never deleted, so a plain NOT NULL FK (NO ACTION) can
    # never orphan or lose audit rows.
    site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    # Application-validated against SiteLogAuditAction.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    actor: Mapped["User"] = relationship(
        lazy="joined", foreign_keys=[actor_user_id]
    )

    __table_args__ = (
        Index("ix_slog_audit_event_created", "site_log_event_id", "created_at"),
    )


class CaptureEligibilityTransition(Base):
    """Append-only, group-scoped eligibility history.

    Current state = highest ``transition_no`` for the event (assigned
    monotonically inside the writing transaction with the parent event
    row locked; concurrent writers conflict on the tenant-scoped UNIQUE
    and retry). ``created_at`` is display metadata only — ordering
    authority is ``transition_no``, never timestamps or UUIDs.

    The first transition on creation is NULL → eligibility_pending_
    unexposed (founder ruling N1, closed). Demotion to development_only
    is one-way; ``reason`` records why (including
    ``development_released``, which is admin-only at the API layer and
    absent from builder UI).
    """

    __tablename__ = "capture_eligibility_transitions"

    transition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=_TENANT_DEFAULT
    )

    site_log_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("site_log_events.site_log_event_id"),
        nullable=False,
    )

    transition_no: Mapped[int] = mapped_column(Integer, nullable=False)

    from_state: Mapped[CaptureEligibilityState | None] = mapped_column(
        SqlaEnum(
            CaptureEligibilityState,
            name="capture_eligibility_state",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    to_state: Mapped[CaptureEligibilityState] = mapped_column(
        SqlaEnum(
            CaptureEligibilityState,
            name="capture_eligibility_state",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "transition_no",
            name="uq_slog_eligibility_transition_no",
        ),
        CheckConstraint(
            "transition_no >= 1", name="ck_slog_eligibility_no_ge_1"
        ),
        # First transition has no from_state; every later one must.
        CheckConstraint(
            "(transition_no = 1 AND from_state IS NULL) OR "
            "(transition_no > 1 AND from_state IS NOT NULL)",
            name="ck_slog_eligibility_from_state",
        ),
        Index(
            "ix_slog_eligibility_event_no", "site_log_event_id", "transition_no"
        ),
    )
