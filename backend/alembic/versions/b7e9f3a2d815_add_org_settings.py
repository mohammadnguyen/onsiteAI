"""add org_settings singleton (default_day_hours)

Founder decision 2026-08-24 (labour day-entry costing): a labour entry
recorded as attendance only (day_fraction, no hours) previously carried
NO cost — ``hours * rate_snapshot`` is null when hours is null — so
day-based records showed $0 in every rollup. The founder's model: a day
IS worth a configurable number of hours (their norm: 10), so cost for
an hours-less entry derives as ``day_fraction * default_day_hours *
rate_snapshot`` AT READ TIME. Days stay days — entries are never
rewritten into hours, and changing the setting deliberately re-prices
historical day-only entries (the parameter is a pricing rule, not a
per-entry fact; rate_snapshot stays write-once per entry as before).

This migration adds the singleton ``org_settings`` table that stores
the parameter:

* ``settings_id`` UUID pk (house convention)
* ``default_day_hours`` NUMERIC(4,2) NOT NULL DEFAULT 10.00,
  CHECK 0 < value <= 24
* a UNIQUE expression index on ``(true)`` enforces at most ONE row at
  the DB level (the service layer get-or-creates idempotently)
* the row is seeded here so existing deployments read 10.00 immediately

Additive and reversible: downgrade drops the table. No existing data is
touched — cost stays computed-on-read, never stored (money-integrity
doctrine unchanged).

Revision ID: b7e9f3a2d815
Revises: a9b8c7d6e5f4
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e9f3a2d815"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_settings",
        sa.Column("settings_id", sa.UUID(), nullable=False),
        sa.Column(
            "default_day_hours",
            sa.Numeric(precision=4, scale=2),
            server_default=sa.text("10.00"),
            nullable=False,
        ),
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
            "default_day_hours > 0 AND default_day_hours <= 24",
            name="ck_org_settings_default_day_hours",
        ),
        sa.PrimaryKeyConstraint("settings_id"),
    )
    op.create_index(
        "uq_org_settings_singleton",
        "org_settings",
        [sa.text("(true)")],
        unique=True,
    )
    # Seed the single row so live deployments serve the default (10.00)
    # without a first-write. Fixed UUID keeps the seed deterministic.
    op.execute(
        "INSERT INTO org_settings (settings_id) "
        "VALUES ('5e771e60-0000-4000-8000-000000000001'::uuid)"
    )


def downgrade() -> None:
    op.drop_index("uq_org_settings_singleton", table_name="org_settings")
    op.drop_table("org_settings")
