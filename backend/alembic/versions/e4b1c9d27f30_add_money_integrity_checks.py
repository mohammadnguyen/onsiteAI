"""add money-integrity CHECK constraints

Audit findings T-1 / B-4. The ``inc = ex + gst`` GST invariant and the
non-negative money guards were enforced only in scattered Python paths and
the Pydantic layer, with no DB backstop — so a code path that skipped the
reconcile (structured create with both components, lone-component PATCH, the
reviewer-resolve path) could silently persist a triple that does not
reconcile, corrupting job rollups and the accountant Excel totals.

This additive, reversible migration adds the authoritative DB backstops:

* ``ck_expenses_gst_components_sum`` — amount_ex_gst + gst_amount = amount_inc_gst
* ``ck_expenses_amounts_nonneg``     — the three expense money columns >= 0
* ``ck_jobs_contract_value_nonneg``  — contract_value_ex_gst is NULL or >= 0
* ``ck_jobs_total_budget_nonneg``    — total_budget_ex_gst is NULL or >= 0
* ``ck_job_category_budgets_amount_nonneg`` — budget_amount_ex_gst >= 0

All existing rows already satisfy these (every derived split sets
gst = inc - ex, cash sets gst = 0, and all money values are positive), so the
constraints validate cleanly against current data. NO data is rewritten.

Reversible: downgrade drops all five constraints.

Revision ID: e4b1c9d27f30
Revises: a7c4e2f10d3b
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b1c9d27f30"
down_revision: Union[str, Sequence[str], None] = "a7c4e2f10d3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_expenses_gst_components_sum",
        "expenses",
        "amount_ex_gst + gst_amount = amount_inc_gst",
    )
    op.create_check_constraint(
        "ck_expenses_amounts_nonneg",
        "expenses",
        "amount_inc_gst >= 0 AND amount_ex_gst >= 0 AND gst_amount >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_contract_value_nonneg",
        "jobs",
        "contract_value_ex_gst IS NULL OR contract_value_ex_gst >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_total_budget_nonneg",
        "jobs",
        "total_budget_ex_gst IS NULL OR total_budget_ex_gst >= 0",
    )
    op.create_check_constraint(
        "ck_job_category_budgets_amount_nonneg",
        "job_category_budgets",
        "budget_amount_ex_gst >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_job_category_budgets_amount_nonneg",
        "job_category_budgets",
        type_="check",
    )
    op.drop_constraint("ck_jobs_total_budget_nonneg", "jobs", type_="check")
    op.drop_constraint("ck_jobs_contract_value_nonneg", "jobs", type_="check")
    op.drop_constraint("ck_expenses_amounts_nonneg", "expenses", type_="check")
    op.drop_constraint("ck_expenses_gst_components_sum", "expenses", type_="check")
