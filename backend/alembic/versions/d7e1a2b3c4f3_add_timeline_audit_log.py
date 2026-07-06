"""add timeline_audit_log

Job Timeline PR 1 (4/4). Append-only audit log for timeline changes,
reusing the ``job_audit_log`` idiom:

* ``timeline_item_id`` is a plain column with **no** hard FK, so audit
  rows stay valid after their item is soft-deleted.
* ``created_at`` defaults to ``clock_timestamp()`` (not ``now()``) so
  several audit inserts inside one transaction keep distinct,
  DESC-orderable timestamps.

FKs target ``jobs.job_id`` and ``users.user_id``. Purely additive: no
existing table or row is modified.

Revision ID: d7e1a2b3c4f3
Revises: d7e1a2b3c4f2
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e1a2b3c4f3"
down_revision: Union[str, Sequence[str], None] = "d7e1a2b3c4f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "timeline_audit_log",
        sa.Column("audit_id", sa.UUID(), nullable=False),
        # Intentionally NOT a hard FK: the audit row must survive its
        # timeline item being soft-deleted (or a future hard delete).
        sa.Column("timeline_item_id", sa.UUID(), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=False),
        # "create" | "update" | "soft_delete" | "status_change" —
        # application-validated; no DB CHECK so new actions need no
        # follow-up migration.
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # ``clock_timestamp()`` (NOT ``now()``) so rapid inserts in one
        # transaction get distinct real-time values for ORDER BY DESC.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_timeline_audit_log_item",
        "timeline_audit_log",
        ["timeline_item_id"],
    )
    op.create_index(
        "ix_timeline_audit_log_job_created",
        "timeline_audit_log",
        ["job_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_timeline_audit_log_job_created", table_name="timeline_audit_log"
    )
    op.drop_index(
        "ix_timeline_audit_log_item", table_name="timeline_audit_log"
    )
    op.drop_table("timeline_audit_log")
