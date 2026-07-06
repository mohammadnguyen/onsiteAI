"""add timeline_items

Job Timeline PR 1 (2/4). Creates the single-table timeline record plus
its three ENUM types (``timeline_item_type`` / ``issue_status`` /
``issue_severity``). FKs target ``jobs.job_id``, ``users.user_id``, and
``job_checklist_items.checklist_item_id`` (created in the prior
migration).

The two ENUM types are created explicitly up front (mirroring the
review-queue migration idiom) so the columns can reference named types;
``downgrade`` drops the tables' constraints with the table and then the
ENUM types, keeping the round-trip clean.

Purely additive: no existing table or row is modified.

Revision ID: d7e1a2b3c4f1
Revises: d7e1a2b3c4f0
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e1a2b3c4f1"
down_revision: Union[str, Sequence[str], None] = "d7e1a2b3c4f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create the three ENUM types explicitly (create_type=False on the
    # column references below) so the round-trip has a single, named
    # source of truth to DROP on downgrade.
    timeline_item_type = postgresql.ENUM(
        "daily_note",
        "photo",
        "issue",
        "delay",
        "variation",
        "inspection",
        "completion",
        name="timeline_item_type",
        create_type=False,
    )
    timeline_item_type.create(op.get_bind(), checkfirst=False)

    issue_status = postgresql.ENUM(
        "open",
        "resolved",
        "closed",
        name="issue_status",
        create_type=False,
    )
    issue_status.create(op.get_bind(), checkfirst=False)

    issue_severity = postgresql.ENUM(
        "low",
        "medium",
        "high",
        name="issue_severity",
        create_type=False,
    )
    issue_severity.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "timeline_items",
        sa.Column("timeline_item_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column(
            "item_type",
            postgresql.ENUM(
                "daily_note",
                "photo",
                "issue",
                "delay",
                "variation",
                "inspection",
                "completion",
                name="timeline_item_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        # issue-only (NULL for non-issue rows)
        sa.Column(
            "status",
            postgresql.ENUM(
                "open",
                "resolved",
                "closed",
                name="issue_status",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM(
                "low",
                "medium",
                "high",
                name="issue_severity",
                create_type=False,
            ),
            nullable=True,
        ),
        # associations
        sa.Column("checklist_item_id", sa.UUID(), nullable=True),
        sa.Column("assigned_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "requires_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        # time + attribution
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        # An issue must always carry a status.
        sa.CheckConstraint(
            "item_type != 'issue' OR status IS NOT NULL",
            name="ck_timeline_items_issue_requires_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"],
            ["job_checklist_items.checklist_item_id"],
        ),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("timeline_item_id"),
    )
    op.create_index(
        "ix_timeline_items_job_occurred",
        "timeline_items",
        ["job_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_timeline_items_job_type",
        "timeline_items",
        ["job_id", "item_type"],
    )
    op.create_index(
        "ix_timeline_items_active",
        "timeline_items",
        ["job_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_timeline_items_checklist_item",
        "timeline_items",
        ["checklist_item_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_timeline_items_checklist_item", table_name="timeline_items"
    )
    op.drop_index("ix_timeline_items_active", table_name="timeline_items")
    op.drop_index("ix_timeline_items_job_type", table_name="timeline_items")
    op.drop_index(
        "ix_timeline_items_job_occurred", table_name="timeline_items"
    )
    op.drop_table("timeline_items")
    # Autogenerate never emits DROP TYPE; these are mandatory hand-edits
    # for a clean downgrade + upgrade round-trip.
    op.execute("DROP TYPE IF EXISTS issue_severity")
    op.execute("DROP TYPE IF EXISTS issue_status")
    op.execute("DROP TYPE IF EXISTS timeline_item_type")
