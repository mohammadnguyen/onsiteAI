"""add job target profit and warning thresholds

Phase 3 Lite+ — Budget clarity + target margin settings. Adds three
nullable percent columns to ``jobs`` so the user can express a target
profit margin and per-job amber/red warning thresholds for the budget
chip on the dashboard.

* ``target_profit_ratio_pct`` — target profit margin as a percent.
  NULL = not set. Range constraint ``0 <= x < 100``.
* ``warning_amber_pct`` / ``warning_red_pct`` — per-job thresholds for
  the budget chip's banding scheme. Both nullable; NULL means "use
  the system default" (resolved at the API boundary, never written
  back to the column). DB constraints: amber non-negative, red strictly
  positive, and amber strictly less than red when both are set.

Stored values are intentionally never overwritten with the 80 / 100
defaults — the fallback lives in a single service helper and surfaces
on the API only via the separate ``effective_warning_*`` summary fields.
That separation is what point 3 of the operator review (2026-05-10)
required.

Pydantic enforces the same rules at the API boundary so callers see a
422 before reaching the DB. The CHECK constraints below are the
backstop for callers that bypass Pydantic — admin SQL scripts, future
API clients with stale validation, etc.

Revision ID: b3e7a8f1c042
Revises: 9e7c97b85cfd
Create Date: 2026-05-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3e7a8f1c042"
down_revision: Union[str, Sequence[str], None] = "9e7c97b85cfd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the three columns plus four CHECK constraints to ``jobs``."""
    op.add_column(
        "jobs",
        sa.Column(
            "target_profit_ratio_pct",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "warning_amber_pct",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "warning_red_pct",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
        ),
    )

    # CHECK constraints — defense-in-depth backstop behind Pydantic.
    # All four are NULL-safe so existing rows (where the new columns
    # default to NULL) and partial settings (only amber set, only red
    # set, neither set) are accepted.
    op.create_check_constraint(
        "ck_jobs_target_profit_ratio_pct_range",
        "jobs",
        "target_profit_ratio_pct IS NULL OR "
        "(target_profit_ratio_pct >= 0 AND target_profit_ratio_pct < 100)",
    )
    op.create_check_constraint(
        "ck_jobs_warning_amber_pct_nonneg",
        "jobs",
        "warning_amber_pct IS NULL OR warning_amber_pct >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_warning_red_pct_positive",
        "jobs",
        "warning_red_pct IS NULL OR warning_red_pct > 0",
    )
    op.create_check_constraint(
        "ck_jobs_warning_amber_lt_red",
        "jobs",
        "warning_amber_pct IS NULL OR warning_red_pct IS NULL OR "
        "warning_amber_pct < warning_red_pct",
    )


def downgrade() -> None:
    """Drop the four CHECK constraints, then the three columns.

    Order matters: constraints reference the columns, so they must be
    dropped first. A downgrade + upgrade round-trip is part of the
    Batch 1 verification gate.
    """
    op.drop_constraint(
        "ck_jobs_warning_amber_lt_red", "jobs", type_="check"
    )
    op.drop_constraint(
        "ck_jobs_warning_red_pct_positive", "jobs", type_="check"
    )
    op.drop_constraint(
        "ck_jobs_warning_amber_pct_nonneg", "jobs", type_="check"
    )
    op.drop_constraint(
        "ck_jobs_target_profit_ratio_pct_range", "jobs", type_="check"
    )
    op.drop_column("jobs", "warning_red_pct")
    op.drop_column("jobs", "warning_amber_pct")
    op.drop_column("jobs", "target_profit_ratio_pct")
