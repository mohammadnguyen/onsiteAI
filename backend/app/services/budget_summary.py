"""Phase 3 Lite — budget aggregation service (extended in Phase 3 Lite+).

Pure read-only aggregation over Phase 1 (jobs, categories,
job_category_budgets) and Phase 2 (expenses). HTTP-agnostic; reuses the
:class:`~app.services.jobs.JobNotFound` domain exception so the API
layer can map it onto a 404 with the same convention as every other
``GET /jobs/{id}`` route.

Phase 3 Lite inclusion rule (frozen by ``docs/phase-3-lite-plan.md``):

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

Phase 3 Lite+ extensions (frozen by ``docs/phase-3-lite-plus-plan.md``):

* :func:`_effective_thresholds` is the single source of the system
  default amber/red (80.00 / 100.00). Stored values on
  :class:`Job` stay nullable; the defaults surface only via the
  ``effective_warning_*_pct`` fields on :class:`JobSummary` /
  :class:`JobBudgetSummary`. Per the operator review (point 3,
  2026-05-10), the stored columns are never written back with the
  fallback.
* :func:`compute_band` is the canonical chip-band routing rule. It is
  exercised by backend tests so the contract is guarded even though
  the chip itself is rendered by the admin UI. ``Over budget`` only
  fires when ``percent_consumed >= 100`` OR ``remaining_ex_gst < 0``
  (point 2 of the operator review).
* :func:`_compute_margin_fields` derives ``target_cost_limit_ex_gst``,
  ``budgeted_profit_ex_gst``, ``budgeted_profit_ratio_pct``, and
  ``budget_delta_vs_target_cost_ex_gst`` from the job's contract value,
  target profit ratio, and total budget. Mid-project actual profit is
  intentionally **not** computed: future costs are not knowable, so
  framing ``contract − cost_to_date`` as profit would be misleading.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

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

# Frozen by the Phase 3 Lite plan. Anywhere this set changes (e.g. adding
# a future ``deleted`` value or splitting reviewed-vs-pending banding)
# must come with an explicit phase-plan amendment, not a quiet edit here.
_INCLUDED_STATUSES: tuple[ReviewStatus, ...] = (
    ReviewStatus.reviewed,
    ReviewStatus.pending,
)

_ZERO = Decimal("0.00")
_ONE_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")

# Phase 3 Lite+ system defaults for the warning chip bands. Single source
# of truth — anything that needs the effective threshold goes through
# :func:`_effective_thresholds`. NEVER written back to the stored
# ``warning_amber_pct`` / ``warning_red_pct`` columns.
DEFAULT_WARNING_AMBER_PCT = Decimal("80.00")
DEFAULT_WARNING_RED_PCT = Decimal("100.00")


# Band codes returned by :func:`compute_band`. The frontend's
# ``BudgetChip`` renders these one-to-one. ``critical`` is new in
# Phase 3 Lite+ — it covers the case where the user set a custom red
# threshold below 100 and consumption crossed it, but the budget has
# not actually been exceeded.
Band = Literal[
    "on_track",
    "approaching",
    "critical",
    "over_budget",
    "no_budget",
]


def _q(value: Decimal) -> Decimal:
    """Quantize a money value to 0.01. Centralised so quantization can't drift."""
    return value.quantize(_ONE_CENT)


def _effective_thresholds(
    stored_amber: Decimal | None,
    stored_red: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Resolve the (effective_amber, effective_red) pair for a job.

    NULL stored values fall back to :data:`DEFAULT_WARNING_AMBER_PCT` /
    :data:`DEFAULT_WARNING_RED_PCT`. The stored values themselves are
    never modified — this helper exists so the defaults live in exactly
    one place and the API can expose stored vs effective separately.
    """
    return (
        stored_amber if stored_amber is not None else DEFAULT_WARNING_AMBER_PCT,
        stored_red if stored_red is not None else DEFAULT_WARNING_RED_PCT,
    )


def compute_band(
    percent_consumed: Decimal | None,
    remaining_ex_gst: Decimal | None,
    total_budget_ex_gst: Decimal | None,
    eff_amber_pct: Decimal,
    eff_red_pct: Decimal,
) -> Band:
    """Return the chip band code for a job's consumption snapshot.

    Frozen routing rules (point 2 of the 2026-05-10 operator review):

    * ``no_budget`` — ``total_budget_ex_gst`` is NULL or 0
    * ``over_budget`` — ``percent_consumed >= 100`` **OR**
      ``remaining_ex_gst < 0``. This is the only band that may carry
      the wording "Over budget" in the UI.
    * ``critical`` — ``eff_red_pct <= percent_consumed < 100`` (only
      reachable when the user set a custom red threshold below 100;
      with the default 100, this band collapses to empty and the next
      band reached is ``over_budget``).
    * ``approaching`` — ``eff_amber_pct <= percent_consumed <
      eff_red_pct``
    * ``on_track`` — ``percent_consumed < eff_amber_pct``

    Tie-break order (highest severity wins):
    ``over_budget > critical > approaching > on_track``.
    """
    if total_budget_ex_gst is None or total_budget_ex_gst == 0:
        return "no_budget"
    # Over-budget rule is checked first so a custom red below 100 cannot
    # mislabel an actually-exceeded budget as merely "critical".
    over_by_percent = (
        percent_consumed is not None and percent_consumed >= _HUNDRED
    )
    over_by_remaining = (
        remaining_ex_gst is not None and remaining_ex_gst < _ZERO
    )
    if over_by_percent or over_by_remaining:
        return "over_budget"
    if percent_consumed is None:
        # Defensive: if budget is set but percent couldn't be computed,
        # return ``no_budget`` rather than guess at a band.
        return "no_budget"
    if percent_consumed >= eff_red_pct:
        return "critical"
    if percent_consumed >= eff_amber_pct:
        return "approaching"
    return "on_track"


def _compute_margin_fields(
    contract: Decimal | None,
    target_profit_ratio_pct: Decimal | None,
    total_budget: Decimal | None,
) -> tuple[
    Decimal | None,  # target_cost_limit_ex_gst
    Decimal | None,  # budgeted_profit_ex_gst
    Decimal | None,  # budgeted_profit_ratio_pct
    Decimal | None,  # budget_delta_vs_target_cost_ex_gst
]:
    """Derive the four Phase 3 Lite+ margin fields from job inputs.

    Each field is ``None`` when its required inputs are missing — see
    the nullable-rules table in ``docs/phase-3-lite-plus-plan.md``.
    """
    target_cost_limit: Decimal | None = None
    budgeted_profit: Decimal | None = None
    budgeted_profit_ratio: Decimal | None = None
    budget_delta: Decimal | None = None

    if contract is not None and target_profit_ratio_pct is not None:
        target_cost_limit = _q(
            contract * (_HUNDRED - target_profit_ratio_pct) / _HUNDRED
        )

    if contract is not None and total_budget is not None:
        budgeted_profit = _q(contract - total_budget)
        if contract > 0:
            budgeted_profit_ratio = (
                budgeted_profit / contract * _HUNDRED
            ).quantize(_ONE_CENT)

    if target_cost_limit is not None and total_budget is not None:
        budget_delta = _q(total_budget - target_cost_limit)

    return (
        target_cost_limit,
        budgeted_profit,
        budgeted_profit_ratio,
        budget_delta,
    )


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


def _build_job_summary(
    actual_inc: Decimal,
    actual_ex: Decimal,
    gst: Decimal,
    total_budget: Decimal | None,
    stored_amber: Decimal | None,
    stored_red: Decimal | None,
) -> JobSummary:
    """Construct a :class:`JobSummary` with effective thresholds resolved."""
    remaining, percent, overspend = _job_metrics(actual_ex, total_budget)
    eff_amber, eff_red = _effective_thresholds(stored_amber, stored_red)
    return JobSummary(
        actual_inc_gst=_q(actual_inc),
        actual_ex_gst=_q(actual_ex),
        gst_amount=_q(gst),
        total_budget_ex_gst=total_budget,
        remaining_ex_gst=remaining,
        percent_consumed=percent,
        overspend=overspend,
        effective_warning_amber_pct=eff_amber,
        effective_warning_red_pct=eff_red,
    )


async def summarize_jobs(
    db: AsyncSession,
    *,
    job_ids: Sequence[uuid.UUID] | None = None,
) -> dict[uuid.UUID, JobSummary]:
    """Return per-job summaries keyed by ``job_id``.

    Two-query strategy: one ``SELECT`` over jobs (now also fetching the
    stored warning thresholds), one aggregate ``SELECT`` over expenses
    grouped by job. Jobs with no expenses appear in the result with
    all-zero actuals so the caller can render every row without a null
    check. The effective thresholds are always populated on the
    returned :class:`JobSummary` (stored override OR system default).

    Pass ``job_ids=[]`` to short-circuit to an empty result. Pass
    ``job_ids=None`` to summarise every job in the DB.
    """
    if job_ids is not None and len(job_ids) == 0:
        return {}

    # Fetch the stored thresholds alongside the budget so the threshold
    # fallback can be applied per-row in one pass.
    jobs_q = select(
        Job.job_id,
        Job.total_budget_ex_gst,
        Job.warning_amber_pct,
        Job.warning_red_pct,
    )
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
    for job_id, total_budget, stored_amber, stored_red in job_rows:
        agg = agg_rows.get(job_id)
        if agg is None:
            out[job_id] = _build_job_summary(
                _ZERO, _ZERO, _ZERO, total_budget, stored_amber, stored_red
            )
            continue
        out[job_id] = _build_job_summary(
            Decimal(agg.actual_inc_gst),
            Decimal(agg.actual_ex_gst),
            Decimal(agg.gst_amount),
            total_budget,
            stored_amber,
            stored_red,
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

    Phase 3 Lite+ extension: the returned envelope carries the four
    derived margin fields plus the two effective thresholds. See the
    nullable-rules table in ``docs/phase-3-lite-plus-plan.md``.
    """
    job = (
        await db.execute(select(Job).where(Job.job_id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFound(job_id)

    # Reuse ``summarize_jobs`` for the job-level numbers + effective
    # thresholds so the math stays single-sourced.
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

    # CHP-7: actual ex-GST for expenses on this job whose
    # ``category_id IS NULL``. Surfaced as a separate top-level field
    # on ``JobBudgetSummary`` so the per-category list stays strictly
    # typed (every row has a real ``category_id``), while still letting
    # the UI render an "Uncategorised: $X" line that makes the
    # category totals reconcile with the job-level ``actual_ex_gst``.
    uncategorised_q = select(
        func.coalesce(func.sum(Expense.amount_ex_gst), _ZERO).label("uncategorised")
    ).where(
        Expense.job_id == job_id,
        Expense.review_status.in_(_INCLUDED_STATUSES),
        Expense.category_id.is_(None),
    )
    uncategorised_ex = Decimal(
        (await db.execute(uncategorised_q)).scalar_one()
    )

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

    # Phase 3 Lite+ derived margin fields. Inputs all come from the
    # ``Job`` row (contract value, target profit, total budget); none
    # touch the expense aggregate.
    (
        target_cost_limit,
        budgeted_profit,
        budgeted_profit_ratio,
        budget_delta,
    ) = _compute_margin_fields(
        job.contract_value_ex_gst,
        job.target_profit_ratio_pct,
        job.total_budget_ex_gst,
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
        target_profit_ratio_pct=job.target_profit_ratio_pct,
        target_cost_limit_ex_gst=target_cost_limit,
        budgeted_profit_ex_gst=budgeted_profit,
        budgeted_profit_ratio_pct=budgeted_profit_ratio,
        budget_delta_vs_target_cost_ex_gst=budget_delta,
        effective_warning_amber_pct=job_summary.effective_warning_amber_pct,
        effective_warning_red_pct=job_summary.effective_warning_red_pct,
        uncategorised_actual_ex_gst=_q(uncategorised_ex),
        categories=rows,
    )
