"""add job_checklist_items

Job Timeline PR 1 (1/4). Creates the minimal per-job checklist table.
Built first because ``timeline_items`` (next migration) carries a FK to
``job_checklist_items.checklist_item_id`` — the referenced table must
exist before the referencing one.

Purely additive: CREATE TABLE + CREATE INDEX only; no existing table or
row is touched.

Revision ID: d7e1a2b3c4f0
Revises: f2c3d4e5a6b7
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e1a2b3c4f0"
down_revision: Union[str, Sequence[str], None] = "f2c3d4e5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_checklist_items",
        sa.Column("checklist_item_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        # Phase 2 template engine; reserved.
        sa.Column("phase", sa.String(length=100), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_done",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.UUID(), nullable=True),
        # Phase 2 evidence enforcement; reserved.
        sa.Column(
            "requires_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(["done_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("checklist_item_id"),
    )
    op.create_index(
        "ix_job_checklist_items_job",
        "job_checklist_items",
        ["job_id"],
    )
    op.create_index(
        "ix_job_checklist_items_active",
        "job_checklist_items",
        ["job_id", "sort_order"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_job_checklist_items_active", table_name="job_checklist_items"
    )
    op.drop_index(
        "ix_job_checklist_items_job", table_name="job_checklist_items"
    )
    op.drop_table("job_checklist_items")
