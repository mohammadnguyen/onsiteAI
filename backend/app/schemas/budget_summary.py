"""Phase 3 Lite — budget summary schemas.

Public-facing wire shapes for the dashboard / budget-visibility surface.
Three schemas:

* :class:`JobSummary` — embedded inside :class:`~app.schemas.job.JobPublic`
  on ``GET /jobs`` so the list page can render `Spent inc GST`,
  `Spent ex GST`, `Budget ex GST`, `Remaining ex GST`, and `% consumed`
  per row.
* :class:`CategoryBudgetRow` — one row in the per-category breakdown
  returned from ``GET /jobs/{job_id}/budget-summary``.
* :class:`JobBudgetSummary` — full envelope returned from
  ``GET /jobs/{job_id}/budget-summary``: the same per-job totals as
  :class:`JobSummary` plus the list of category rows.

GST-basis convention: ``actual_inc_gst`` is display only ("what was paid
in cash terms"); all budget comparisons are ex-GST. ``gst_amount`` equals
``actual_inc_gst − actual_ex_gst`` and is shown so users can sanity-check
the inclusive total. ``remaining_ex_gst`` and ``percent_consumed`` are
``None`` when ``total_budget_ex_gst`` is NULL or zero — see the data-model
section of ``docs/phase-3-lite-plan.md`` for the rationale.

All ``Decimal`` fields serialise as strings (Pydantic v2 default) so the
admin client can render them without floating-point round-trip drift.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel


class JobSummary(BaseModel):
    """Per-job aggregate; embedded in :class:`JobPublic` on ``GET /jobs``.

    ``remaining_ex_gst`` and ``percent_consumed`` are ``None`` when no
    budget is set (NULL or zero). ``overspend`` is always a bool — it
    is ``False`` (not ``None``) when no budget is set so the UI never
    has to handle a tri-state for the chip.
    """

    actual_inc_gst: Decimal
    actual_ex_gst: Decimal
    gst_amount: Decimal
    total_budget_ex_gst: Decimal | None
    remaining_ex_gst: Decimal | None
    percent_consumed: Decimal | None
    overspend: bool


class CategoryBudgetRow(BaseModel):
    """One row in the per-category breakdown on ``/jobs/{id}/budget-summary``.

    ``budget_ex_gst`` and ``remaining_ex_gst`` are ``None`` when there is
    no ``job_category_budgets`` row for this category — the UI renders
    "—" and a `No budget` chip.
    """

    category_id: uuid.UUID
    category_name: str
    actual_ex_gst: Decimal
    budget_ex_gst: Decimal | None
    remaining_ex_gst: Decimal | None
    overspend: bool


class JobBudgetSummary(BaseModel):
    """Full envelope for ``GET /jobs/{job_id}/budget-summary`` (admin-only).

    Per-job totals (matching :class:`JobSummary`) plus the union of
    categories with either a budget row or at least one non-rejected
    expense. Categories with neither are omitted (no zero-zero rows).
    """

    job_id: uuid.UUID
    actual_inc_gst: Decimal
    actual_ex_gst: Decimal
    gst_amount: Decimal
    total_budget_ex_gst: Decimal | None
    remaining_ex_gst: Decimal | None
    percent_consumed: Decimal | None
    overspend: bool
    categories: list[CategoryBudgetRow]
