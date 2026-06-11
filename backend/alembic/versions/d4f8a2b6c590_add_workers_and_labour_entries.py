"""add workers and labour_entries

Labour v1 (slice L-A): worker roster + daily attendance, in DAYS only —
no payroll concepts anywhere (no rates, wages, hours, overtime, super,
tax). First schema change after real business data began: ADDITIVE
ONLY — two new tables + their indexes; no existing table or row is
touched. The pre-migration backup gate (verified backup/restore
procedure) is mandatory before this runs against staging.

Design notes (mirrors app/models/labour.py):

* ``workers.display_name`` is deliberately NOT unique — names are
  labels, not identity; duplicates disambiguate via ``note``.
* ``labour_entries`` FKs use ``ON DELETE RESTRICT`` so jobs/workers
  with attendance history can never be hard-deleted (the service
  layer's friendly 409 is the contract; RESTRICT is the backstop).
* UNIQUE (worker, job, date): one row per worker/job/day; re-records
  update ``day_fraction``.
* ``day_fraction`` CHECK ∈ {0.5, 1.0}. The cross-row "total per
  worker per date <= 1.0" rule is service-enforced under row locks —
  deliberately NOT a trigger.

Revision ID: d4f8a2b6c590
Revises: c8d3e1f2a079
Create Date: 2026-06-12

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8a2b6c590"
down_revision: Union[str, Sequence[str], None] = "c8d3e1f2a079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default="true", nullable=False
        ),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_table(
        "labour_entries",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column(
            "day_fraction", sa.Numeric(precision=2, scale=1), nullable=False
        ),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "day_fraction IN (0.5, 1.0)",
            name="ck_labour_entries_day_fraction",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["workers.worker_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.job_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("entry_id"),
        sa.UniqueConstraint(
            "worker_id",
            "job_id",
            "work_date",
            name="uq_labour_entries_worker_job_date",
        ),
    )
    op.create_index(
        "ix_labour_entries_job_date",
        "labour_entries",
        ["job_id", "work_date"],
    )
    op.create_index(
        "ix_labour_entries_worker_date",
        "labour_entries",
        ["worker_id", "work_date"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_labour_entries_worker_date", table_name="labour_entries")
    op.drop_index("ix_labour_entries_job_date", table_name="labour_entries")
    op.drop_table("labour_entries")
    op.drop_table("workers")
