"""add timeline_attachments

Job Timeline PR 1 (3/4). Creates the attachment (photo/file) table plus
its ``attachment_upload_status`` ENUM. FKs target
``timeline_items.timeline_item_id`` and ``users.user_id``.

Purely additive: no existing table or row is modified.

Revision ID: d7e1a2b3c4f2
Revises: d7e1a2b3c4f1
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e1a2b3c4f2"
down_revision: Union[str, Sequence[str], None] = "d7e1a2b3c4f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    attachment_upload_status = postgresql.ENUM(
        "pending",
        "confirmed",
        name="attachment_upload_status",
        create_type=False,
    )
    attachment_upload_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "timeline_attachments",
        sa.Column("attachment_id", sa.UUID(), nullable=False),
        sa.Column("timeline_item_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        # evidence metadata (timestamp + GPS + attribution)
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gps_lat", sa.Double(), nullable=True),
        sa.Column("gps_lng", sa.Double(), nullable=True),
        sa.Column(
            "upload_status",
            postgresql.ENUM(
                "pending",
                "confirmed",
                name="attachment_upload_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["timeline_item_id"],
            ["timeline_items.timeline_item_id"],
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("attachment_id"),
    )
    op.create_index(
        "ix_timeline_attachments_item",
        "timeline_attachments",
        ["timeline_item_id"],
    )
    op.create_index(
        "ix_timeline_attachments_active",
        "timeline_attachments",
        ["timeline_item_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_timeline_attachments_active", table_name="timeline_attachments"
    )
    op.drop_index(
        "ix_timeline_attachments_item", table_name="timeline_attachments"
    )
    op.drop_table("timeline_attachments")
    op.execute("DROP TYPE IF EXISTS attachment_upload_status")
