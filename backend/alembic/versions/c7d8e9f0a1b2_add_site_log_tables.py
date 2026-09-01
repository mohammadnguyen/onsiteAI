"""site log: WP A capture tables (Revision 3 + founder rulings O1/O2)

Slice 1 Work Package A, PR A1. Five new tables, purely additive:

* ``site_log_events`` — one row per capture. No content column, no
  ``occurred_at`` (O2: revisions are the single source) and no stored
  ``job_state`` (derived from ``job_id IS NULL``). Device-generated
  ``capture_client_id`` is the offline idempotency key, unique per
  tenant+author.
* ``site_log_event_revisions`` — append-only display/correction/
  withdrawal history. Revision 1 is the as-captured version; corrections
  append and never overwrite; withdrawal requires a reason. The
  immutable Raw Evidence payload is never touched by a revision.
* ``site_log_event_attachments`` — the attachment manifest (O1): the
  server's authoritative statement of how many attachments a capture
  declared, keyed by device-generated ``attachment_client_id`` so
  uploads retry without duplicating Evidence. Enforcement boundary,
  stated precisely: the DATABASE enforces exclusivity (the partial
  unique index keeps one Evidence attached to at most one attachment
  row) and stored⇒bound (the CHECK forbids clearing ``evidence_id``
  while ``state = 'stored'``). Once-only binding — never rebinding a
  non-null ``evidence_id`` to a different Evidence — is NOT
  database-enforced; it is a MANDATORY A2 service invariant, following
  the repository precedent that write-path immutability lives in the
  service layer (``evidence.job_id`` has exactly two explicit writers
  and no DB guard). ``state`` is an operational upload projection — not
  Raw Evidence, not eligibility, not Truth.
* ``site_log_event_audit_log`` — append-only who-did-what trail on the
  job_audit_log precedent; content-free by rule (raw text and file
  content never appear; the revisions table is the content history).
* ``capture_eligibility_transitions`` — append-only group-scoped
  eligibility. Current state = highest ``transition_no`` (monotonic,
  assigned under the parent event row lock); no current_state column
  exists to drift. ``development_released`` is a transition reason,
  never a state.

No Truth, Candidate, Confirmation or Task table is created — WP A ends
at an immutable, job-attributed, human-reviewed SiteLogEvent
(DEC-TRUTH-001 / DEC-AI-BOUNDARY-001 untouched).

Rollback: the downgrade below is structurally complete and safe ONLY
while no real capture exists. Once capture collection begins, the
operational rollback is feature-disable or a forward migration — never
a table drop, because captured Raw Evidence context is retained
material (DEC-EVIDENCE-001).

Revision ID: c7d8e9f0a1b2
Revises: b7e9f3a2d815
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b7e9f3a2d815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")

capture_status_enum = sa.Enum(
    "pending_upload",
    "complete",
    "partial_failed",
    name="site_log_capture_status",
)
attachment_state_enum = sa.Enum(
    "awaiting_upload",
    "pending",
    "stored",
    "failed",
    name="site_log_attachment_state",
)
eligibility_state_enum = sa.Enum(
    "eligibility_pending_unexposed",
    "content_blind_selected",
    "evaluation_locked",
    "first_authorized_evaluation_exposure",
    "evaluated",
    "development_only",
    name="capture_eligibility_state",
)


def upgrade() -> None:
    op.create_table(
        "site_log_events",
        sa.Column("site_log_event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "author_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("capture_client_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "capture_status",
            capture_status_enum,
            nullable=False,
            server_default="pending_upload",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "author_user_id",
            "capture_client_id",
            name="uq_slog_event_capture_client",
        ),
    )
    op.create_index(
        "ix_slog_event_job_created",
        "site_log_events",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_slog_event_author_created",
        "site_log_events",
        ["author_user_id", "created_at"],
    )
    op.create_index(
        "ix_slog_event_unassigned",
        "site_log_events",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("job_id IS NULL"),
    )

    op.create_table(
        "site_log_event_revisions",
        sa.Column("revision_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("internal_location", sa.String(255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "withdrawn",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "revision_no",
            name="uq_slog_revision_no",
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_slog_revision_no_ge_1"
        ),
        sa.CheckConstraint(
            "(NOT withdrawn) OR (reason IS NOT NULL)",
            name="ck_slog_revision_withdrawn_reason",
        ),
        sa.CheckConstraint(
            "(revision_no = 1) OR (reason IS NOT NULL)",
            name="ck_slog_revision_correction_reason",
        ),
    )
    op.create_index(
        "ix_slog_revision_event_no",
        "site_log_event_revisions",
        ["site_log_event_id", "revision_no"],
    )
    op.create_index(
        "ix_slog_revision_occurred",
        "site_log_event_revisions",
        ["occurred_at"],
    )

    op.create_table(
        "site_log_event_attachments",
        sa.Column("attachment_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column("attachment_client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("declared_media_type", sa.String(32), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "evidence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evidence.evidence_id"),
            nullable=True,
        ),
        sa.Column(
            "state",
            attachment_state_enum,
            nullable=False,
            server_default="awaiting_upload",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "attachment_client_id",
            name="uq_slog_attachment_client",
        ),
        sa.CheckConstraint(
            "declared_media_type IN ('audio', 'image', 'text', 'document')",
            name="ck_slog_attachment_media_type",
        ),
        sa.CheckConstraint(
            "declared_size_bytes IS NULL OR declared_size_bytes >= 0",
            name="ck_slog_attachment_size_nonneg",
        ),
        sa.CheckConstraint(
            "state != 'stored' OR evidence_id IS NOT NULL",
            name="ck_slog_attachment_stored_has_evidence",
        ),
    )
    op.create_index(
        "uq_slog_attachment_evidence",
        "site_log_event_attachments",
        ["evidence_id"],
        unique=True,
        postgresql_where=sa.text("evidence_id IS NOT NULL"),
    )
    op.create_index(
        "ix_slog_attachment_event",
        "site_log_event_attachments",
        ["site_log_event_id"],
    )

    op.create_table(
        "site_log_event_audit_log",
        sa.Column("audit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("changed_fields", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_slog_audit_event_created",
        "site_log_event_audit_log",
        ["site_log_event_id", "created_at"],
    )

    op.create_table(
        "capture_eligibility_transitions",
        sa.Column("transition_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=TENANT_DEFAULT,
        ),
        sa.Column(
            "site_log_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_log_events.site_log_event_id"),
            nullable=False,
        ),
        sa.Column("transition_no", sa.Integer(), nullable=False),
        sa.Column("from_state", eligibility_state_enum, nullable=True),
        sa.Column("to_state", eligibility_state_enum, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_log_event_id",
            "transition_no",
            name="uq_slog_eligibility_transition_no",
        ),
        sa.CheckConstraint(
            "transition_no >= 1", name="ck_slog_eligibility_no_ge_1"
        ),
        sa.CheckConstraint(
            "(transition_no = 1 AND from_state IS NULL) OR "
            "(transition_no > 1 AND from_state IS NOT NULL)",
            name="ck_slog_eligibility_from_state",
        ),
    )
    op.create_index(
        "ix_slog_eligibility_event_no",
        "capture_eligibility_transitions",
        ["site_log_event_id", "transition_no"],
    )


def downgrade() -> None:
    # Structurally complete inverse. Operationally valid ONLY against
    # empty tables — see the module docstring's rollback policy.
    op.drop_index(
        "ix_slog_eligibility_event_no",
        table_name="capture_eligibility_transitions",
    )
    op.drop_table("capture_eligibility_transitions")
    op.drop_index(
        "ix_slog_audit_event_created", table_name="site_log_event_audit_log"
    )
    op.drop_table("site_log_event_audit_log")
    op.drop_index(
        "ix_slog_attachment_event", table_name="site_log_event_attachments"
    )
    op.drop_index(
        "uq_slog_attachment_evidence", table_name="site_log_event_attachments"
    )
    op.drop_table("site_log_event_attachments")
    op.drop_index(
        "ix_slog_revision_occurred", table_name="site_log_event_revisions"
    )
    op.drop_index(
        "ix_slog_revision_event_no", table_name="site_log_event_revisions"
    )
    op.drop_table("site_log_event_revisions")
    op.drop_index("ix_slog_event_unassigned", table_name="site_log_events")
    op.drop_index("ix_slog_event_author_created", table_name="site_log_events")
    op.drop_index("ix_slog_event_job_created", table_name="site_log_events")
    op.drop_table("site_log_events")
    eligibility_state_enum.drop(op.get_bind(), checkfirst=True)
    attachment_state_enum.drop(op.get_bind(), checkfirst=True)
    capture_status_enum.drop(op.get_bind(), checkfirst=True)
