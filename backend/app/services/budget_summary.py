"""Phase 3 Lite — budget aggregation service.

Pure read-only aggregation over Phase 1 (jobs, categories,
job_category_budgets) and Phase 2 (expenses). HTTP-agnostic; reuses the
:class:`~app.services.jobs.JobNotFound` domain exception so the API
layer can map it onto a 404 with the same convention as every other
``GET /jobs/{id}`` route.

Inclusion rule (frozen by ``docs/phase-3-lite-plan.md``):

* Aggregations include expenses with ``review_status`` in
  ``{reviewed, pending}`` and **exclude** ``rejected``. Phase 2's
  soft-delete sets ``rejected`` so this matches the existing
  "expense was retracted" semantic.
* ``pending`` is included on purpose: Lite answers the worst-case
  "how much could we owe?" question. Reviewed-vs-pending banding
  layers in at full Phase 3.

Budget math is ex-GST throughout. ``actual_inc_gst`` and ``gst_amount``
are display-only fields surfaced so the user can sanity-check the cash
total against the GST split. The cash-payment GST rule (cash →
``gst_amount = 0``, ``amount_ex = amount_inc``) is already absorbed in
each row's ``amount_ex_gst`` by Phase 2's pre-insert listener, so the
aggregate is correct without special-casing payment method here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.category import Category
from app.models.expense import Expense, ReviewStatus
from app.models.job import Job, JobCategoryBudget
from app.schemas.budget_summary import (
    CategoryBudgetRow,
    JobBudgetSummary,
    JobSummary,
)
from app.services.jobs import JobNotFound

# Frozen by the plan. Anywhere this set changes (e.g. adding a future
# ``deleted`` value or splitting reviewed-vs-pending banding) must come
# with an explicit phase-plan amendment, not a quiet edit here.
_INCLUDED_STATUSES: tuple[ReviewStatus, ...] = (
    ReviewStatus.reviewed,
    ReviewStatus.pending,
)

_ZERO = Decimal("0.00")
_ONE_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")


def _q(value: Decimal) -> Decimal:
    """Quantize a money value to 0.01. Centralised so quantization can't drift."""
    return value.quantize(_ONE_CENT)


def _job_metrics(
    actual_ex: Decimal,
    total_budget: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, bool]:
    """Compute ``(remaining_ex_gst, percent_consumed, overspend)``.

    NULL or zero ``total_budget`` collapses to the same result —
    ``(None, None, False)``. The plan treats zero-budget the same as
    "no budget set" for both display and overspend decision-making.
    """
    if total_budget is None or total_budget == 0:
        return (None, None, False)
    remaining = _q(total_budget - actual_ex)
    percent = ((_HUNDRED * actual_ex) / total_budget).quantize(_ONE_CENT)
    overspend = actual_ex > total_budget
    return (remaining, percent, overspend)


def _zero_summary(total_budget: Decimal | None) -> JobSummary:
    """Build a JobSummary for a job that has no non-rejected expenses.

    All actuals are 0.00; remaining equals the budget when set.
    """
    remaining, percent, overspend = _job_metrics(_ZERO, total_budget)
    return JobSummary(
        actual_inc_gst=_ZERO,
        actual_ex_gst=_ZERO,
        gst_amount=_ZERO,
        total_budget_ex_gst=total_budget,
        remaining_ex_gst=remaining,
        percent_consumed=percent,
        overspend=overspend,
    )


async def summarize_jobs(
    db: AsyncSession,
    *,
    job_ids: Sequence[uuid.UUID] | None = None,
) -> dict[uuid.UUID, JobSummary]:
    """Return per-job summaries keyed by ``job_id``.

    Two-query strategy: one ``SELECT job_id, total_budget_ex_gst FROM
    jobs`` (filtered to ``job_ids`` when provided), one aggregate
    ``SELECT job_id, SUM(...) FROM expenses ... GROUP BY job_id`` over
    the included statuses. Jobs with no expenses appear in the result
    with all-zero actuals so the caller can render every row without a
    null check.

    Pass ``job_ids=[]`` to short-circuit to an empty result. Pass
    ``job_ids=None`` to summarise every job in the DB.
    """
    # Empty list short-circuit — avoids an IN () SQL error and is the
    # natural answer when the caller has zero jobs to summarise.
    if job_ids is not None and len(job_ids) == 0:
        return {}

    jobs_q = select(Job.job_id, Job.total_budget_ex_gst)
    if job_ids is not None:
        jobs_q = jobs_q.where(Job.job_id.in_(job_ids))
    job_rows = (await db.execute(jobs_q)).all()

    agg_q = (
        select(
            Expense.job_id.label("job_id"),
            func.coalesce(func.sum(Expense.amount_inc_gst), _ZERO).label(
                "actual_inc_gst"
            ),
            func.coalesce(func.sum(Expense.amount_ex_gst), _ZERO).label(
                "actual_ex_gst"
            ),
            func.coalesce(func.sum(Expense.gst_amount), _ZERO).label("gst_amount"),
        )
        .where(Expense.review_status.in_(_INCLUDED_STATUSES))
        .group_by(Expense.job_id)
    )
    if job_ids is not None:
        agg_q = agg_q.where(Expense.job_id.in_(job_ids))
    agg_rows = {r.job_id: r for r in (await db.execute(agg_q)).all()}

    out: dict[uuid.UUID, JobSummary] = {}
    for job_id, total_budget in job_rows:
        agg = agg_rows.get(job_id)
        if agg is None:
            out[job_id] = _zero_summary(total_budget)
            continue
        actual_inc = Decimal(agg.actual_inc_gst)
        actual_ex = Decimal(agg.actual_ex_gst)
        gst = Decimal(agg.gst_amount)
        remaining, percent, overspend = _job_metrics(actual_ex, total_budget)
        out[job_id] = JobSummary(
            actual_inc_gst=_q(actual_inc),
            actual_ex_gst=_q(actual_ex),
            gst_amount=_q(gst),
            total_budget_ex_gst=total_budget,
            remaining_ex_gst=remaining,
            percent_consumed=percent,
            overspend=overspend,
        )
    return out


async def summarize_job(
    db: AsyncSession, job_id: uuid.UUID
) -> JobBudgetSummary:
    """Per-job summary including the per-category breakdown.

    Raises :class:`JobNotFound` if the id doesn't resolve. Categories
    list rule: include every category that has either a budget row OR
    at least one non-rejected expense on this job. Categories with
    neither are omitted (no zero-zero rows). Expenses with
    ``category_id IS NULL`` still count toward the job-level totals
    but do not appear in the category list.
    """
    job = (
        await db.execute(select(Job).where(Job.job_id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFound(job_id)

    # Reuse ``summarize_jobs`` for the job-level numbers so the math
    # stays single-sourced. The ``job_ids=[job_id]`` filter is critical:
    # without it we'd pull every job in the DB.
    summaries = await summarize_jobs(db, job_ids=[job_id])
    job_summary = summaries[job_id]

    # Per-category actual totals. ``category_id IS NULL`` rows are
    # filtered out — see the categories-list rule above.
    cat_actual_q = (
        select(
            Expense.category_id.label("category_id"),
            func.coalesce(func.sum(Expense.amount_ex_gst), _ZERO).label(
                "actual_ex_gst"
            ),
        )
        .where(
            Expense.job_id == job_id,
            Expense.review_status.in_(_INCLUDED_STATUSES),
            Expense.category_id.is_not(None),
        )
        .group_by(Expense.category_id)
    )
    cat_actuals: dict[uuid.UUID, Decimal] = {
        r.category_id: Decimal(r.actual_ex_gst)
        for r in (await db.execute(cat_actual_q)).all()
    }

    # Per-category budgets. Eager-load the joined Category so we have
    # the name without a per-row lazy fetch.
    bud_q = (
        select(JobCategoryBudget)
        .where(JobCategoryBudget.job_id == job_id)
        .options(selectinload(JobCategoryBudget.category))
    )
    budgets: dict[uuid.UUID, JobCategoryBudget] = {
        b.category_id: b for b in (await db.execute(bud_q)).scalars().all()
    }

    # For categories that have actual spend but no budget row, fetch
    # the names in one round trip.
    actual_only_ids = set(cat_actuals.keys()) - set(budgets.keys())
    actual_only_names: dict[uuid.UUID, str] = {}
    if actual_only_ids:
        name_q = select(Category).where(Category.category_id.in_(actual_only_ids))
        for cat in (await db.execute(name_q)).scalars().all():
            actual_only_names[cat.category_id] = cat.category_name

    rows: list[CategoryBudgetRow] = []
    for cid in set(cat_actuals.keys()) | set(budgets.keys()):
        actual_ex = cat_actuals.get(cid, _ZERO)
        budget_row = budgets.get(cid)
        if budget_row is not None:
            budget_ex: Decimal | None = budget_row.budget_amount_ex_gst
            cat_name = budget_row.category.category_name
        else:
            budget_ex = None
            cat_name = actual_only_names[cid]
        if budget_ex is not None and budget_ex > 0:
            remaining: Decimal | None = _q(budget_ex - actual_ex)
            overspend = actual_ex > budget_ex
        else:
            remaining = None
            overspend = False
        rows.append(
            CategoryBudgetRow(
                category_id=cid,
                category_name=cat_name,
                actual_ex_gst=_q(actual_ex),
                budget_ex_gst=budget_ex,
                remaining_ex_gst=remaining,
                overspend=overspend,
            )
        )

    return JobBudgetSummary(
        job_id=job_id,
        actual_inc_gst=job_summary.actual_inc_gst,
        actual_ex_gst=job_summary.actual_ex_gst,
        gst_amount=job_summary.gst_amount,
        total_budget_ex_gst=job_summary.total_budget_ex_gst,
        remaining_ex_gst=job_summary.remaining_ex_gst,
        percent_consumed=job_summary.percent_consumed,
        overspend=job_summary.overspend,
        categories=rows,
    )
