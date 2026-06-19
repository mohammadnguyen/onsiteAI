"""add jobs.gst_mode

F2 — per-job contract GST basis. Adds an additive ``gst_mode`` enum
column to ``jobs``:

* ``inclusive`` — UI "Including GST" (contract entered gross; the mobile
  client stores ex-GST = entered / 1.1).
* ``exclusive`` — UI "No GST (Cash)" (contract entered as no-GST / cash
  revenue; stored as-is; GST = 0).

Display-hint only: ``contract_value_ex_gst`` remains the canonical ex-GST
basis and the backend runs NO GST math on gst_mode (margin / budget /
export are unchanged). Existing rows backfill to ``exclusive`` via the
server_default, preserving today's exact behaviour — NO contract value is
ever rewritten. The internal value "exclusive" is never user-visible.

Reversible: downgrade drops the column then the enum type.

Revision ID: a7c4e2f10d3b
Revises: c5d9e8a1b2f4
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7c4e2f10d3b"
down_revision: Union[str, Sequence[str], None] = "c5d9e8a1b2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the gst_mode enum type and add the NOT NULL column.

    server_default 'exclusive' backfills every existing job so behaviour
    is byte-identical to before the migration.
    """
    gst_mode = postgresql.ENUM(
        "inclusive", "exclusive", name="gst_mode", create_type=False
    )
    gst_mode.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "jobs",
        sa.Column(
            "gst_mode",
            gst_mode,
            nullable=False,
            server_default="exclusive",
        ),
    )


def downgrade() -> None:
    """Drop the column then the enum type (reverse order of upgrade)."""
    op.drop_column("jobs", "gst_mode")
    gst_mode = postgresql.ENUM(
        "inclusive", "exclusive", name="gst_mode", create_type=False
    )
    gst_mode.drop(op.get_bind(), checkfirst=False)
