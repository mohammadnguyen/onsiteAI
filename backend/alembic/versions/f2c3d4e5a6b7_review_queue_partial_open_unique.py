"""review queue: one OPEN row per expense (partial unique index)

Audit findings D-6 / T-2. The review queue's uniqueness was a plain
``UNIQUE(expense_id)`` — one row per expense for ALL TIME — which made the
review lifecycle a one-way dead end: once any queue row existed (even a
resolved/rejected one), the expense could never be re-queued, so a future
re-flag-on-edit or "send back to review" action would raise an IntegrityError
and roll the whole request back as a 500.

This replaces it with a PARTIAL unique index that enforces at most one row
whose ``status='open'`` per expense, matching ADR 0001's stated "one open row
per expense" while allowing closed history rows to accumulate.

No current code path inserts a second queue row, so this is behaviour-neutral
for existing data (each expense still has at most one row today); it unblocks a
supported re-review path.

Reversible: downgrade restores the full unique constraint. (A downgrade would
fail only if an expense had accumulated 2+ rows — impossible under current code
and this migration's own history-preserving intent.)

Revision ID: f2c3d4e5a6b7
Revises: e4b1c9d27f30
Create Date: 2026-07-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2c3d4e5a6b7"
down_revision: Union[str, Sequence[str], None] = "e4b1c9d27f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_expense_review_queue_expense_id",
        "expense_review_queue",
        type_="unique",
    )
    op.create_index(
        "uq_expense_review_queue_one_open",
        "expense_review_queue",
        ["expense_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_expense_review_queue_one_open",
        table_name="expense_review_queue",
    )
    op.create_unique_constraint(
        "uq_expense_review_queue_expense_id",
        "expense_review_queue",
        ["expense_id"],
    )
