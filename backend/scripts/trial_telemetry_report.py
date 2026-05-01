"""Read-only trial telemetry report.

Surfaces the data that's already in the dev DB:
  * Per-expense parser output (raw_input, amount, job, supplier, category,
    confidence, payment, review_status, duplicate_flag, duplicate_of)
  * Review queue state (review_reasons, status, resolved_by, resolved_at)
  * Admin audit history (changed_fields diff, edit reasons)
  * Aggregate counts (status split, per-job spend, duplicate hits, queue
    resolutions, audit edits)

Applies the baseline-exclusion rule from docs/trial-baseline.md: the 3
Claude E2E rows entered by jeffrey@example.com on Kelly House between
2026-04-24T10:02:14Z and 2026-04-24T10:03:11Z are listed separately and
NOT counted in summary totals.

Usage from the ``backend/`` directory::

    uv run python -X utf8 -m scripts.trial_telemetry_report

The ``-X utf8`` flag is needed on Windows so zh raw_input_text doesn't
trip cp1252. The script is idempotent + read-only — no writes, no
schema changes.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_engine, get_sessionmaker

# Locked at trial baseline (docs/trial-baseline.md). Do not edit at runtime.
TRIAL_START = datetime(2026, 4, 24, 9, 31, 17, tzinfo=timezone.utc)

# Three Claude E2E rows from before the framework freeze. Excluded from
# summary aggregates but listed in the dedicated section so the report is
# still complete.
BASELINE_EXCLUDED_EXPENSE_IDS = {
    "a5511974-8b21-40be-a9ee-7916013cb2dc",  # bunnings $500 现金 Kelly bluemetal
    "fee039a9-6718-4d4c-a682-5915cf99c8bf",  # 水泥 $1000 转账 Kelly
    "50f4edc8-a50f-4647-a734-c2f13972deaa",  # $200 Bunnings Kelly timber
}


def _h1(text: str) -> None:
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def _h2(text: str) -> None:
    print()
    print(f"### {text}")
    print()


def _money(d: Decimal | None) -> str:
    if d is None:
        return "       —"
    return f"${d:>10,.2f}"


async def _fetch_expenses(db: AsyncSession) -> list[dict[str, Any]]:
    sql = """
        SELECT
            e.expense_id::text         AS expense_id,
            e.created_at,
            e.expense_date,
            e.amount_inc_gst,
            e.amount_ex_gst,
            e.gst_amount,
            e.raw_input_text,
            e.description,
            e.review_status,
            e.payment_method,
            e.confidence_score,
            e.duplicate_flag,
            e.duplicate_of_expense_id::text AS duplicate_of_id,
            j.job_name,
            s.supplier_name,
            c.category_name,
            u.email AS entered_by
        FROM expenses e
        JOIN jobs j         ON e.job_id              = j.job_id
        LEFT JOIN suppliers s ON e.supplier_id       = s.supplier_id
        LEFT JOIN categories c ON e.category_id      = c.category_id
        JOIN users u        ON e.entered_by_user_id  = u.user_id
        ORDER BY e.created_at ASC
    """
    rows = (await db.execute(text(sql))).mappings().fetchall()
    return [dict(r) for r in rows]


async def _fetch_review_queue(db: AsyncSession) -> dict[str, dict[str, Any]]:
    sql = """
        SELECT
            rq.expense_id::text  AS expense_id,
            rq.review_reasons,
            rq.status,
            rq.opened_at,
            rq.resolved_at,
            ru.email             AS resolved_by,
            rq.resolution_notes
        FROM expense_review_queue rq
        LEFT JOIN users ru ON rq.resolved_by_user_id = ru.user_id
    """
    rows = (await db.execute(text(sql))).mappings().fetchall()
    return {r["expense_id"]: dict(r) for r in rows}


async def _fetch_audit(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    sql = """
        SELECT
            al.expense_id::text  AS expense_id,
            al.edited_at,
            al.changed_fields,
            al.reason,
            u.email              AS edited_by
        FROM expense_audit_log al
        JOIN users u ON al.edited_by_user_id = u.user_id
        ORDER BY al.edited_at ASC
    """
    rows = (await db.execute(text(sql))).mappings().fetchall()
    by_expense: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_expense[r["expense_id"]].append(dict(r))
    return by_expense


def _enum(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _print_expense(
    n: int,
    e: dict[str, Any],
    queue: dict[str, dict[str, Any]],
    audit: dict[str, list[dict[str, Any]]],
    expenses_by_id: dict[str, dict[str, Any]],
) -> None:
    excluded = e["expense_id"] in BASELINE_EXCLUDED_EXPENSE_IDS
    flag = "  [EXCLUDED — Claude E2E baseline noise]" if excluded else ""
    print(f"--- Expense {n}{flag} ---")
    print(f"  expense_id     : {e['expense_id']}")
    print(f"  created_at     : {e['created_at'].isoformat()}")
    print(f"  expense_date   : {e['expense_date']}")
    print(f"  entered_by     : {e['entered_by']}")
    print(f"  raw_input_text : {e['raw_input_text']!r}")
    print(f"  parsed:")
    print(f"    amount_inc   : {_money(e['amount_inc_gst'])}")
    print(f"    amount_ex    : {_money(e['amount_ex_gst'])}")
    print(f"    gst          : {_money(e['gst_amount'])}")
    print(f"    job          : {e['job_name']}")
    print(f"    supplier     : {e['supplier_name'] or '(none)'}")
    print(f"    category     : {e['category_name'] or '(none)'}")
    print(f"    payment      : {_enum(e['payment_method'])}")
    print(f"    description  : {e['description'] or '(none)'}")
    conf = e["confidence_score"]
    print(f"    confidence   : {conf if conf is not None else '(none)'}")
    print(f"  review_status  : {_enum(e['review_status'])}")
    if e["duplicate_flag"]:
        target = e["duplicate_of_id"]
        target_text = expenses_by_id.get(target, {}).get("raw_input_text") if target else None
        target_str = (
            f"{target} (raw: {target_text!r})" if target_text else target or "(unknown)"
        )
        print(f"  duplicate_flag : YES — duplicate_of: {target_str}")
    else:
        print(f"  duplicate_flag : no")

    rq = queue.get(e["expense_id"])
    if rq:
        reasons = rq["review_reasons"] or []
        reason_strs = [_enum(r) for r in reasons]
        print(f"  review_queue   :")
        print(f"    reasons      : {', '.join(reason_strs) if reason_strs else '(none)'}")
        print(f"    status       : {_enum(rq['status'])}")
        print(f"    opened_at    : {rq['opened_at'].isoformat() if rq['opened_at'] else '—'}")
        if rq["resolved_at"]:
            print(f"    resolved_at  : {rq['resolved_at'].isoformat()}")
            print(f"    resolved_by  : {rq['resolved_by']}")
            if rq["resolution_notes"]:
                print(f"    notes        : {rq['resolution_notes']!r}")

    rows = audit.get(e["expense_id"], [])
    if rows:
        print(f"  audit_history  : {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")
        for a in rows:
            print(f"    [{a['edited_at'].isoformat()}] by {a['edited_by']}")
            for field, change in (a["changed_fields"] or {}).items():
                old = change.get("old") if isinstance(change, dict) else None
                new = change.get("new") if isinstance(change, dict) else None
                print(f"        {field}: {old!r} → {new!r}")
            if a["reason"]:
                print(f"        reason: {a['reason']!r}")
    print()


async def _main() -> None:
    Session = get_sessionmaker()
    try:
        async with Session() as db:
            expenses = await _fetch_expenses(db)
            queue = await _fetch_review_queue(db)
            audit = await _fetch_audit(db)
    finally:
        await get_engine().dispose()

    expenses_by_id = {e["expense_id"]: e for e in expenses}
    real = [e for e in expenses if e["expense_id"] not in BASELINE_EXCLUDED_EXPENSE_IDS]
    excluded = [e for e in expenses if e["expense_id"] in BASELINE_EXCLUDED_EXPENSE_IDS]

    now = datetime.now(timezone.utc)
    elapsed = now - TRIAL_START

    _h1("SiteTracker — trial telemetry report")
    print(f"Trial start         : {TRIAL_START.isoformat()}")
    print(f"Report generated    : {now.isoformat()}")
    print(f"Elapsed             : {elapsed}")
    print(f"Total expenses in DB: {len(expenses)} ({len(real)} real, {len(excluded)} excluded)")

    _h2("Status split (real trial entries only)")
    by_status: dict[str, int] = defaultdict(int)
    for e in real:
        by_status[_enum(e["review_status"])] += 1
    for status in ("reviewed", "pending", "rejected"):
        print(f"  {status:10s} : {by_status.get(status, 0)}")

    _h2("Per-job spend (real trial entries only — sums all statuses)")
    per_job_count: dict[str, int] = defaultdict(int)
    per_job_sum: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    per_job_reviewed: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    per_job_rejected_count: dict[str, int] = defaultdict(int)
    for e in real:
        per_job_count[e["job_name"]] += 1
        per_job_sum[e["job_name"]] += e["amount_inc_gst"]
        if _enum(e["review_status"]) == "reviewed":
            per_job_reviewed[e["job_name"]] += e["amount_inc_gst"]
        elif _enum(e["review_status"]) == "rejected":
            per_job_rejected_count[e["job_name"]] += 1
    for job in sorted(per_job_count):
        print(
            f"  {job:20s} : {per_job_count[job]} expense(s), "
            f"all-status total {_money(per_job_sum[job])}, "
            f"reviewed-only total {_money(per_job_reviewed[job])}, "
            f"rejected count {per_job_rejected_count[job]}"
        )

    _h2("Parser-output diagnostics (real trial entries only)")
    with_raw = sum(1 for e in real if e["raw_input_text"])
    duplicate_flagged = sum(1 for e in real if e["duplicate_flag"])
    print(f"  expenses with raw_input_text     : {with_raw} of {len(real)}")
    print(f"  duplicate_flag fired             : {duplicate_flagged}")
    confidences = [e["confidence_score"] for e in real if e["confidence_score"] is not None]
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        print(
            f"  confidence score (n={len(confidences)})   : "
            f"avg {avg_conf:.2f}, min {min(confidences):.2f}, max {max(confidences):.2f}"
        )

    _h2("Review queue activity (all expenses; queue rows cascade with expense delete)")
    by_q_status: dict[str, int] = defaultdict(int)
    real_ids = {e["expense_id"] for e in real}
    for exp_id, rq in queue.items():
        if exp_id in real_ids:
            by_q_status[_enum(rq["status"])] += 1
    for status in ("open", "resolved", "rejected"):
        print(f"  {status:10s} : {by_q_status.get(status, 0)}")

    _h2("Admin audit activity (real trial entries only)")
    audit_real = sum(len(audit.get(e["expense_id"], [])) for e in real)
    print(f"  audit log rows (status transitions + admin edits) : {audit_real}")

    _h1("Per-expense detail")
    for n, e in enumerate(real, 1):
        _print_expense(n, e, queue, audit, expenses_by_id)

    if excluded:
        _h1("Baseline-excluded rows (Claude E2E noise — listed for completeness, NOT counted above)")
        for n, e in enumerate(excluded, 1):
            _print_expense(n, e, queue, audit, expenses_by_id)

    _h2("Notes")
    print(
        "  These counts are descriptive (what happened in the system),"
    )
    print(
        "  not classified (which six-tag bucket each event belongs to)."
    )
    print(
        "  The six-tag classification still requires admin judgment;"
    )
    print(
        "  see docs/internal-testing.md → Issue log template."
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main())
