"""evidence: raw-evidence and audit tables (DEC-EVIDENCE-001)

Evidence Storage foundation slice. Two new tables, purely additive:

* ``evidence`` — one row per raw evidence object (voice/photo/text/
  document). ``occurred_at`` is stored separately from ``created_at``
  (DEC-TIME-001). ``job_id`` is nullable and CONFIRMED-ONLY — written
  exclusively by explicit user action (DEC-JOB-ATTR-001). No delete
  columns exist: raw evidence is never destroyed by any normal product
  path (DEC-EVIDENCE-001); a future privileged purge would be its own
  promoted decision and migration.
* ``evidence_audit_log`` — append-only lifecycle trail, following the
  ``job_audit_log`` precedent (single-tenant server_default tenant_id,
  ``clock_timestamp()`` created_at).

Reversible: downgrade drops both tables and both enums. Safe while the
feature is unused; once real evidence exists, downgrading would discard
those rows — staging/production run this manually per ADR 0003 with
that understanding.

Revision ID: a9b8c7d6e5f4
Revises: f2c3d4e5a6b7
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f2c3d4e5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

media_type_enum = sa.Enum(
    "audio", "image", "text", "document", name="evidence_media_type"
)
status_enum = sa.Enum("pending", "stored", "failed", name="evidence_status")


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("evidence_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("media_type", media_type_enum, nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("storage_backend", sa.String(16), nullable=True),
        sa.Column("storage_key", sa.String(255), nullable=True, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
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
    )
    op.create_index("ix_evidence_job_created", "evidence", ["job_id", "created_at"])
    op.create_index(
        "ix_evidence_uploader_created",
        "evidence",
        ["uploaded_by_user_id", "created_at"],
    )
    op.create_index("ix_evidence_sha256", "evidence", ["sha256"])
    op.create_index("ix_evidence_status", "evidence", ["status"])

    op.create_table(
        "evidence_audit_log",
        sa.Column("audit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text(
                "'00000000-0000-0000-0000-000000000001'::uuid"
            ),
        ),
        sa.Column(
            "evidence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evidence.evidence_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evidence_audit_log_tenant_evidence_created",
        "evidence_audit_log",
        ["tenant_id", "evidence_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_audit_log_tenant_evidence_created",
        table_name="evidence_audit_log",
    )
    op.drop_table("evidence_audit_log")
    op.drop_index("ix_evidence_status", table_name="evidence")
    op.drop_index("ix_evidence_sha256", table_name="evidence")
    op.drop_index("ix_evidence_uploader_created", table_name="evidence")
    op.drop_index("ix_evidence_job_created", table_name="evidence")
    op.drop_table("evidence")
    status_enum.drop(op.get_bind(), checkfirst=True)
    media_type_enum.drop(op.get_bind(), checkfirst=True)
