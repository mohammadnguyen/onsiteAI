"""add labour rates and hours

Labour v2 (slice L-C1): labour COST CAPTURE, not payroll — a worker's
current hourly rate, optional hours per attendance entry, and a
write-once rate snapshot per entry. Labour cost = hours * rate_snapshot
is computed on read and NEVER stored as money. No wages/super/tax/
overtime concepts.

SECOND schema change of the real-data era: ADDITIVE ONLY — three new
NULLABLE columns + their CHECK constraints. No existing column, row, or
constraint is touched; NO backfill (existing entries keep NULL hours +
NULL rate_snapshot, so they simply carry no cost). The pre-migration
backup gate (verified backup/restore procedure) is MANDATORY before
this runs against staging.

Columns:
* ``workers.hourly_rate`` NUMERIC(8,2) NULL, CHECK >= 0 — admin-managed
  current rate; snapshotted onto new entries only.
* ``labour_entries.hours`` NUMERIC(4,2) NULL, CHECK (0 < hours <= 24) —
  optional duration; independent of ``day_fraction``.
* ``labour_entries.rate_snapshot`` NUMERIC(8,2) NULL, CHECK >= 0 — the
  worker's rate copied at entry CREATE time; write-once (the service
  never refreshes it), so a later rate change cannot rewrite history.

Revision ID: f3a1c8d9e024
Revises: d4f8a2b6c590
Create Date: 2026-06-12

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c8d9e024"
down_revision: Union[str, Sequence[str], None] = "d4f8a2b6c590"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (additive only)."""
    op.add_column(
        "workers",
        sa.Column("hourly_rate", sa.Numeric(precision=8, scale=2), nullable=True),
    )
    op.create_check_constraint(
        "ck_workers_hourly_rate", "workers", "hourly_rate >= 0"
    )

    op.add_column(
        "labour_entries",
        sa.Column("hours", sa.Numeric(precision=4, scale=2), nullable=True),
    )
    op.add_column(
        "labour_entries",
        sa.Column(
            "rate_snapshot", sa.Numeric(precision=8, scale=2), nullable=True
        ),
    )
    op.create_check_constraint(
        "ck_labour_entries_hours",
        "labour_entries",
        "hours > 0 AND hours <= 24",
    )
    op.create_check_constraint(
        "ck_labour_entries_rate_snapshot",
        "labour_entries",
        "rate_snapshot >= 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_labour_entries_rate_snapshot", "labour_entries", type_="check"
    )
    op.drop_constraint("ck_labour_entries_hours", "labour_entries", type_="check")
    op.drop_column("labour_entries", "rate_snapshot")
    op.drop_column("labour_entries", "hours")
    op.drop_constraint("ck_workers_hourly_rate", "workers", type_="check")
    op.drop_column("workers", "hourly_rate")
