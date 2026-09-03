"""Report data for the PDF expense report (founder decision 2026-08-24).

Read-only. Shares the Excel export's FROZEN inclusion rule via
:func:`fetch_export_expenses` — reviewed always, pending only on
explicit opt-in, rejected never — so the two accountant-facing exports
can never disagree about which expenses exist.

Division of labour (CLAUDE.md architecture rule): every aggregate is
computed here; the client formats and paginates, and never sums money.

Scope note (DEC-BOUNDARY-ACCT-001, Charter §50): this is a summary of
the tenant's own recorded expenses. It is deliberately NOT a general
ledger and carries no BAS/tax framing or substantiation advice —
Forey does not reproduce accounting software.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense, ReceiptStatus
from app.services.budget_summary import summarize_jobs
from app.services.excel_export import fetch_export_expenses

_ZERO = Decimal("0.00")


def _job_of(e: Expense) -> tuple[uuid.UUID, str]:
    return e.job_id, (e.job.job_name if e.job else "")


async def build_report_data(
    db: AsyncSession,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    job_id: uuid.UUID | None = None,
    include_pending: bool = False,
) -> dict:
    """Assemble the report payload for the given filters.

    Period figures are derived from the filtered expense set. Budget
    position (``total_budget_ex_gst`` / ``remaining_ex_gst`` /
    ``percent_consumed`` / ``overspend`` / ``all_time_ex_gst``) comes
    from :func:`summarize_jobs`, which is NOT date-filtered on purpose:
    a budget is consumed over the life of the job, not over the report
    window. Mixing the two would make "84% consumed" mean different
    things on different exports.

    Jobs with no expenses in the period are omitted — a report page for
    a project with nothing on it is noise.
    """
    expenses = await fetch_export_expenses(
        db,
        from_date=from_date,
        to_date=to_date,
        job_id=job_id,
        include_pending=include_pending,
    )

    tot_inc = tot_gst = tot_ex = _ZERO
    on_file = expected_later = 0
    per_job: dict[uuid.UUID, dict] = {}
    per_cat: dict[uuid.UUID | None, dict] = {}
    per_month: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    rows: list[dict] = []

    for e in expenses:
        tot_inc += e.amount_inc_gst
        tot_gst += e.gst_amount
        tot_ex += e.amount_ex_gst

        # No "attached" receipt state exists yet (Phase 5 adds it), so
        # `on_file` is structurally 0 today. Counted, never editorialised.
        if e.receipt_status == ReceiptStatus.expected_later:
            expected_later += 1

        jid, jname = _job_of(e)
        j = per_job.setdefault(
            jid,
            {
                "job_id": jid,
                "job_name": jname,
                "job_code": e.job.job_code if e.job else None,
                "site_address": e.job.site_address if e.job else None,
                "contract_value_ex_gst": (
                    e.job.contract_value_ex_gst if e.job else None
                ),
                "expense_count": 0,
                "period_inc_gst": _ZERO,
                "period_gst": _ZERO,
                "period_ex_gst": _ZERO,
            },
        )
        j["expense_count"] += 1
        j["period_inc_gst"] += e.amount_inc_gst
        j["period_gst"] += e.gst_amount
        j["period_ex_gst"] += e.amount_ex_gst

        cid = e.category_id
        c = per_cat.setdefault(
            cid,
            {
                "category_id": cid,
                "category_name": (
                    e.category.category_name if e.category else None
                ),
                "actual_ex_gst": _ZERO,
                "actual_inc_gst": _ZERO,
            },
        )
        c["actual_ex_gst"] += e.amount_ex_gst
        c["actual_inc_gst"] += e.amount_inc_gst

        per_month[e.expense_date.strftime("%Y-%m")] += e.amount_inc_gst

        rows.append(
            {
                "expense_date": e.expense_date,
                "job_id": jid,
                "job_name": jname,
                "supplier_name": (
                    e.supplier.supplier_name if e.supplier else None
                ),
                "category_name": (
                    e.category.category_name if e.category else None
                ),
                "description": e.description,
                "amount_inc_gst": e.amount_inc_gst,
                "gst_amount": e.gst_amount,
                "amount_ex_gst": e.amount_ex_gst,
                "payment_method": e.payment_method.value,
                "receipt_status": e.receipt_status.value,
                "entered_by": e.entered_by.email if e.entered_by else "",
            }
        )

    # All-time budget position for exactly the jobs that appear.
    summaries = await summarize_jobs(db, job_ids=list(per_job.keys()))
    jobs: list[dict] = []
    for jid, j in per_job.items():
        s = summaries.get(jid)
        jobs.append(
            {
                **j,
                "total_budget_ex_gst": s.total_budget_ex_gst if s else None,
                "all_time_ex_gst": s.actual_ex_gst if s else _ZERO,
                "remaining_ex_gst": s.remaining_ex_gst if s else None,
                "percent_consumed": s.percent_consumed if s else None,
                "overspend": bool(s.overspend) if s else False,
            }
        )
    # Biggest spender first — the report leads with where the money went.
    jobs.sort(key=lambda r: r["period_inc_gst"], reverse=True)

    categories = sorted(
        per_cat.values(), key=lambda r: r["actual_ex_gst"], reverse=True
    )
    months = [
        {"month": m, "actual_inc_gst": v} for m, v in sorted(per_month.items())
    ]

    return {
        "meta": {
            "from_date": from_date,
            "to_date": to_date,
            "generated_at": datetime.now(tz=UTC),
            "include_pending": include_pending,
            "job_count": len(jobs),
            "expense_count": len(rows),
        },
        "totals": {
            "actual_inc_gst": tot_inc,
            "actual_ex_gst": tot_ex,
            "gst_amount": tot_gst,
            "receipts_on_file": on_file,
            "receipts_expected_later": expected_later,
        },
        "jobs": jobs,
        "categories": categories,
        "months": months,
        "expenses": rows,
    }
