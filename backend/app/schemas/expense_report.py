"""Shapes for ``GET /reports/expenses-report`` — the PDF report's data.

Admin-only, read-only. Every aggregate the report renders (totals,
per-job rollups, category and month series, receipt counts) is computed
SERVER-side: the client formats and lays out, it never sums money
(CLAUDE.md architecture rule — no business logic in the frontend).

Money and quantities travel as ``Decimal``, serialised as JSON strings
like every other money surface in this API.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ReportMeta(BaseModel):
    """What this export covers — rendered in the report's sub-header."""

    from_date: date | None
    to_date: date | None
    generated_at: datetime
    include_pending: bool
    job_count: int
    expense_count: int


class ReportTotals(BaseModel):
    """Period totals across every included expense."""

    actual_inc_gst: Decimal
    actual_ex_gst: Decimal
    gst_amount: Decimal
    # Receipts: the schema currently has no "attached" state (Phase 5
    # introduces it), so ``receipts_on_file`` is 0 for every tenant
    # today. Exposed as a plain count, never as a compliance warning —
    # a not-yet-built feature is not a finding.
    receipts_on_file: int
    receipts_expected_later: int


class ReportJobRow(BaseModel):
    """One project: period spend plus its all-time budget position.

    Period figures come from the filtered expense set; ``all_time_*``
    and the budget fields come from :func:`summarize_jobs`, which is
    deliberately NOT date-filtered — a budget is consumed over the life
    of the job, not the report window.
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    job_name: str
    job_code: str | None
    site_address: str | None
    expense_count: int
    period_inc_gst: Decimal
    period_gst: Decimal
    period_ex_gst: Decimal
    contract_value_ex_gst: Decimal | None
    total_budget_ex_gst: Decimal | None
    all_time_ex_gst: Decimal
    remaining_ex_gst: Decimal | None
    percent_consumed: Decimal | None
    overspend: bool


class ReportCategoryRow(BaseModel):
    """Spend by category over the period. Uncategorised is its own row
    with a null ``category_id`` — never silently folded into a total."""

    category_id: uuid.UUID | None
    category_name: str | None
    actual_ex_gst: Decimal
    actual_inc_gst: Decimal


class ReportMonthRow(BaseModel):
    """One calendar month of the period, for the monthly column chart."""

    month: str  # YYYY-MM
    actual_inc_gst: Decimal


class ReportExpenseRow(BaseModel):
    """One line item, in the report's column order."""

    expense_date: date
    job_id: uuid.UUID
    job_name: str
    supplier_name: str | None
    category_name: str | None
    description: str | None
    amount_inc_gst: Decimal
    gst_amount: Decimal
    amount_ex_gst: Decimal
    payment_method: str
    receipt_status: str
    entered_by: str


class ExpenseReportData(BaseModel):
    """The whole report payload."""

    meta: ReportMeta
    totals: ReportTotals
    jobs: list[ReportJobRow]
    categories: list[ReportCategoryRow]
    months: list[ReportMonthRow]
    expenses: list[ReportExpenseRow]
