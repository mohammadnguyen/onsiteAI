"""add job_audit_log

Job Lifecycle v1A-1: foundation table for auditing job edits and
lifecycle events. Mirrors the expense_audit_log shape but with:

* ``tenant_id`` column (forward-compat for multi-tenant; populated by
  DB server_default in V1 because no User.tenant_id field exists yet).
* Nullable ``job_id`` with ``ON DELETE SET NULL`` so audit history
  survives the future hard-delete-empty-job action (v1A-3).
* Snapshot columns (``job_name_snapshot``, ``job_code_snapshot``) for
  traceability after the parent job is gone.
* ``action`` column ("edit" | "archive" | "reopen" | "delete") for
  quick filtering without parsing changed_fields.
* Composite index ``(tenant_id, job_id, created_at)`` to support both
  per-job audit-trail queries and forward-compat tenant-wide queries.

The migration is purely additive (CREATE TABLE + CREATE INDEX); it
does not touch any existing table or row.

Revision ID: c8d3e1f2a079
Revises: b3e7a8f1c042
Create Date: 2026-05-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d3e1f2a079"
down_revision: Union[str, Sequence[str], None] = "b3e7a8f1c042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_audit_log",
        sa.Column("audit_id", sa.UUID(), nullable=False),
        # V1 single-tenant default. When multi-tenancy ships, a
        # follow-up migration drops this server_default and the
        # application sets tenant_id from request context.
        sa.Column(
            "tenant_id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text(
                "'00000000-0000-0000-0000-000000000001'::uuid"
            ),
        ),
        # Nullable + SET NULL: keeps the audit row queryable after
        # a future hard-delete-empty-job (v1A-3). The snapshot columns
        # carry the human-meaningful identifier post-delete.
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("job_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("job_code_snapshot", sa.String(length=64), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        # "edit" | "archive" | "reopen" | "delete" — application-
        # validated; no DB CHECK so v1A-3 can add "delete" without a
        # follow-up migration.
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # ``clock_timestamp()`` (NOT ``now()`` / ``transaction_timestamp()``)
        # so each audit-row INSERT inside one transaction gets a distinct
        # real-time value. With ``now()`` all rows in a single test or
        # script-driven batch would share the transaction-start
        # timestamp, breaking ``ORDER BY created_at DESC``.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_job_audit_log_tenant_job_created",
        "job_audit_log",
        ["tenant_id", "job_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_job_audit_log_tenant_job_created",
        table_name="job_audit_log",
    )
    op.drop_table("job_audit_log")
