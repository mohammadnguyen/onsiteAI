"""Phase 2 Task T-J: duplicate-detection stage for the parser pipeline.

Given the key fields of a candidate expense draft, look for a prior
:class:`~app.models.expense.Expense` on the same job that is likely the
same receipt entered twice, and report its ``expense_id``. The rule is
deliberately a **soft match** — the orchestrator never blocks the
save; instead it surfaces a ``duplicate_suspected`` review reason so
an admin can adjudicate.

Matching rule (Phase 2 — frozen):

``same job_id``
AND ``same amount_inc_gst``
AND ``expense_date`` within ±1 calendar day
AND one of:

* both draft and prior have a ``supplier_id`` AND they are equal, OR
* at least one side lacks a ``supplier_id`` AND
  ``normalize_alias(draft.description) == normalize_alias(prior.description)``

Rejected expenses (``review_status = 'rejected'``) are soft-deleted in
Phase 2 and MUST NOT match.

If multiple candidates remain after all four rules, the **earliest**
(by ``created_at ASC``) is returned — we treat it as the canonical
"original" and flag the later row as a possible duplicate.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring):

1. :func:`detect_duplicate` is **async** (DB I/O) but otherwise obeys
   the stage-function contract: it takes the narrow per-field inputs
   and returns a narrow :class:`DuplicateMatch` — never a
   :class:`~app.services.parser.llm_adapter.ParsePartial`.
2. The :class:`~sqlalchemy.ext.asyncio.AsyncSession` is used for
   reads only (``SELECT``). No flush, no commit, no ``session.add``.
3. The SQL filter is the job + amount + date-window triple; the
   supplier/description branch is applied in Python because
   :func:`app.core.text.normalize_alias` is not portably callable
   inside Postgres. The per-parse candidate set is small in practice
   (same job, same amount, ±1 day) so the Python filter is cheap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_alias
from app.models import Expense
from app.models.expense import ReviewStatus


@dataclass(frozen=True)
class DuplicateMatch:
    """Result of the duplicate-detection stage.

    - ``duplicate_of_expense_id``: UUID of the earliest matching prior
      expense, or ``None`` if no match.
    - ``found``: True iff a match was found (bool mirror for ergonomics
      — equivalent to ``duplicate_of_expense_id is not None``).
    """

    duplicate_of_expense_id: uuid.UUID | None
    found: bool


async def detect_duplicate(
    *,
    db: AsyncSession,
    job_id: uuid.UUID,
    amount_inc_gst: Decimal,
    expense_date: date,
    supplier_id: uuid.UUID | None,
    description: str | None,
) -> DuplicateMatch:
    """Soft-match against prior expenses on the same job, within ±1 day,
    same amount, and either same supplier (when both sides have one)
    or same normalized description (when either side lacks a supplier).

    Returns the EARLIEST matching expense_id (by ``created_at`` ASC) if
    any. Never a hard block — the orchestrator surfaces it as a
    ``duplicate_suspected`` review reason.
    """
    # Step 1: narrow candidates via the SQL-expressible criteria.
    # Same job + same amount + ±1 calendar day + not rejected.
    window_lo = expense_date - timedelta(days=1)
    window_hi = expense_date + timedelta(days=1)

    stmt = (
        select(Expense)
        .where(
            Expense.job_id == job_id,
            Expense.amount_inc_gst == amount_inc_gst,
            Expense.expense_date >= window_lo,
            Expense.expense_date <= window_hi,
            Expense.review_status != ReviewStatus.rejected,
        )
        # ``expense_id`` breaks ``created_at`` ties so "earliest is
        # canonical" is deterministic — multi-row captures share the
        # transaction ``created_at`` (audit R13).
        .order_by(Expense.created_at.asc(), Expense.expense_id.asc())
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    # Step 2: Python-side filter on the supplier-or-description branch.
    # "Both sides have a supplier" → suppliers must be equal. Otherwise
    # ("either side lacks a supplier") → normalised descriptions must
    # be equal AND non-empty. An empty normalised description is not a
    # useful signal and should not match another empty string.
    draft_desc_normal = normalize_alias(description) if description is not None else ""

    for prior in candidates:
        if supplier_id is not None and prior.supplier_id is not None:
            # Both sides have a supplier — they must match.
            if prior.supplier_id == supplier_id:
                return DuplicateMatch(duplicate_of_expense_id=prior.expense_id, found=True)
            continue

        # Either side lacks a supplier — descriptions must normalise
        # equal and be non-empty.
        prior_desc_normal = (
            normalize_alias(prior.description) if prior.description is not None else ""
        )
        if draft_desc_normal and draft_desc_normal == prior_desc_normal:
            return DuplicateMatch(duplicate_of_expense_id=prior.expense_id, found=True)

    return DuplicateMatch(duplicate_of_expense_id=None, found=False)
