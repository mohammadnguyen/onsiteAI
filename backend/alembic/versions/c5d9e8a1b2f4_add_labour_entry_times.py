"""add labour entry start/end times

Labour v2.1 (slice L-C3): TIME-RANGE hours capture. Two new NULLABLE
TIME-of-day columns on ``labour_entries`` plus an ordering CHECK. When
both times are set the service DERIVES ``hours`` as the full span
(end - start, no break deduction) and the time range becomes the single
source of truth; when neither is set the existing hours-only behaviour
is preserved (backward compatible).

THIRD schema change of the real-data era: ADDITIVE ONLY — two new
NULLABLE columns + one CHECK constraint. No existing column, row, or
constraint is touched; NO backfill (existing entries keep NULL times, so
their behaviour is unchanged). The pre-migration backup gate (verified
backup/restore procedure) is MANDATORY before this runs against staging.

Columns:
* ``labour_entries.start_time`` TIME NULL — local job-day start of work.
* ``labour_entries.end_time``   TIME NULL — local job-day end of work.

These are TIME-OF-DAY values, NOT timestamps: same-day only, no date and
no timezone. Overnight spans are out of scope.

Constraint:
* ``ck_labour_entries_time_order`` — when both times are present, start
  must precede end (``start_time IS NULL OR end_time IS NULL OR
  start_time < end_time``). A lone time is rejected earlier (422); this
  CHECK is the DB-level backstop.

Revision ID: c5d9e8a1b2f4
Revises: f3a1c8d9e024
Create Date: 2026-06-12

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d9e8a1b2f4"
down_revision: Union[str, Sequence[str], None] = "f3a1c8d9e024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (additive only)."""
    op.add_column(
        "labour_entries",
        sa.Column("start_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "labour_entries",
        sa.Column("end_time", sa.Time(), nullable=True),
    )
    op.create_check_constraint(
        "ck_labour_entries_time_order",
        "labour_entries",
        "start_time IS NULL OR end_time IS NULL OR start_time < end_time",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_labour_entries_time_order", "labour_entries", type_="check"
    )
    op.drop_column("labour_entries", "end_time")
    op.drop_column("labour_entries", "start_time")
