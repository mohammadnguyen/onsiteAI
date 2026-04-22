"""Phase 2 Task T-J: review-reason deriver for the parser pipeline.

Pure synchronous function that evaluates the Phase 2 review triggers
against a fully-populated
:class:`~app.services.parser.llm_adapter.ParsePartial` and returns the
list of :class:`~app.models.review_queue.ReviewReasonCode` values that
fired, in canonical (enum-declaration) order.

Thresholds (Phase 2 — frozen):

=====================  ===========================================  =============================
Signal                 Trigger                                      Reason
=====================  ===========================================  =============================
Amount (primary)       ``amount_conf < 0.8`` OR ``amount_value is   ``amount_uncertain``
                       None``
Currency (primary)     ``unsupported_currency`` is True             ``unsupported_currency``
Job (primary)          ``job_conf < 0.7`` OR ``job_id is None``     ``job_uncertain``
Supplier (primary,     ``expense_type == supplier_expense`` AND     ``supplier_uncertain``
supplier_expense only) ``supplier_conf < 0.7``
Category (secondary)   ``category_conf < 0.6``                      ``category_uncertain``
Duplicate              ``duplicate_flag`` is True                   ``duplicate_suspected``
=====================  ===========================================  =============================

An empty result list means the expense can be saved directly as
``reviewed``; anything non-empty routes it to ``pending`` with the
listed reasons attached to the review queue row.

Payment method is **not** a trigger: an ``unknown`` payment method is
acceptable and does not itself gate review.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring):

1. :func:`derive_review_reasons` is pure synchronous — no I/O, no
   DB, no network. It takes a ``ParsePartial`` read-only and returns
   a new ``list[ReviewReasonCode]``. It never mutates ``parts``.
2. The returned order is fixed to the canonical
   :class:`~app.models.review_queue.ReviewReasonCode` declaration
   order so UI reason chips render consistently.
3. Written as a flat sequence of ``if`` checks — no abstraction, no
   list-of-rules — so every trigger is individually obvious when
   reading the code against the frozen threshold table.
"""

from __future__ import annotations

from app.models import ExpenseType, ReviewReasonCode
from app.services.parser.llm_adapter import ParsePartial


def derive_review_reasons(parts: ParsePartial) -> list[ReviewReasonCode]:
    """Evaluate Phase 2 review triggers against a populated ParsePartial.

    Returns the list of :class:`ReviewReasonCode` values in canonical
    (enum-declaration) order:

        job_uncertain, supplier_uncertain, category_uncertain,
        amount_uncertain, duplicate_suspected, unsupported_currency

    Empty list means the expense can save directly as ``reviewed``.
    Pure synchronous function; does not mutate ``parts``.
    """
    reasons: list[ReviewReasonCode] = []

    # Job (primary).
    if parts.job_id is None or parts.job_conf < 0.7:
        reasons.append(ReviewReasonCode.job_uncertain)

    # Supplier (primary) — only gated on supplier-type expenses. Labour
    # and adjustment rows do not need a supplier, so a low supplier
    # confidence on those kinds is not a review signal.
    if parts.expense_type == ExpenseType.supplier_expense and parts.supplier_conf < 0.7:
        reasons.append(ReviewReasonCode.supplier_uncertain)

    # Category (secondary). Lower bar than the primary signals because
    # category is softer — spelling / sub-category variation shouldn't
    # flood the queue.
    if parts.category_conf < 0.6:
        reasons.append(ReviewReasonCode.category_uncertain)

    # Amount (primary). ``amount_value is None`` implies no amount was
    # extractable at all — explicitly flag regardless of confidence.
    if parts.amount_value is None or parts.amount_conf < 0.8:
        reasons.append(ReviewReasonCode.amount_uncertain)

    # Duplicate — set by the orchestrator after
    # :func:`app.services.parser.duplicates.detect_duplicate` fires.
    if parts.duplicate_flag:
        reasons.append(ReviewReasonCode.duplicate_suspected)

    # Unsupported currency — e.g. "€100" or "￥500" when the parser is
    # only wired for AUD in Phase 2.
    if parts.unsupported_currency:
        reasons.append(ReviewReasonCode.unsupported_currency)

    return reasons
