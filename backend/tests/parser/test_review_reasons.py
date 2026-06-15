"""Phase 2 Task T-J: pure-unit tests for the review-reason deriver.

No DB, no network. Covers the full trigger truth-table for
:func:`app.services.parser.review.derive_review_reasons`:

* each threshold individually
* boundary behaviour (< vs. >=)
* supplier trigger gated by ``expense_type``
* canonical (enum-declaration) order of the output
* payment method variation has no effect
* the input ``ParsePartial`` is not mutated
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict
from decimal import Decimal

import pytest

from app.models import ExpenseType, PaymentMethod, ReviewReasonCode
from app.services.parser.llm_adapter import ParsePartial
from app.services.parser.review import (
    ENRICHMENT_REASONS,
    MONEY_INTEGRITY_REASONS,
    derive_review_reasons,
    gating_reasons,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_high_conf() -> ParsePartial:
    """Build a :class:`ParsePartial` with every primary/secondary signal confidently
    above threshold — baseline with zero triggers firing.
    """
    return ParsePartial(
        raw_text="$305 Bunnings Kelly",
        amount_value=Decimal("305.00"),
        amount_conf=0.95,
        unsupported_currency=False,
        job_id=uuid.uuid4(),
        job_conf=0.95,
        supplier_id=uuid.uuid4(),
        supplier_conf=0.95,
        candidate_supplier_name=None,
        category_id=uuid.uuid4(),
        category_conf=0.95,
        payment_method=PaymentMethod.unknown,
        expense_type=ExpenseType.supplier_expense,
        description="timber",
        duplicate_flag=False,
        duplicate_of_expense_id=None,
        source_per_field={},
    )


# The canonical order the deriver emits matches
# :class:`ReviewReasonCode` declaration order.
CANONICAL_ORDER = [
    ReviewReasonCode.job_uncertain,
    ReviewReasonCode.supplier_uncertain,
    ReviewReasonCode.category_uncertain,
    ReviewReasonCode.amount_uncertain,
    ReviewReasonCode.duplicate_suspected,
    ReviewReasonCode.unsupported_currency,
]


# ---------------------------------------------------------------------------
# Zero-trigger baseline
# ---------------------------------------------------------------------------


def test_all_high_confidence_returns_empty():
    """Every signal above threshold → no reasons (saves as reviewed)."""
    assert derive_review_reasons(_all_high_conf()) == []


# ---------------------------------------------------------------------------
# Amount trigger
# ---------------------------------------------------------------------------


def test_amount_conf_below_threshold():
    """``amount_conf=0.79`` < 0.8 → amount_uncertain."""
    parts = _all_high_conf()
    parts.amount_conf = 0.79
    assert derive_review_reasons(parts) == [ReviewReasonCode.amount_uncertain]


def test_amount_conf_on_threshold_no_trigger():
    """``amount_conf=0.8`` is NOT less than 0.8 → no trigger (boundary)."""
    parts = _all_high_conf()
    parts.amount_conf = 0.8
    assert derive_review_reasons(parts) == []


def test_amount_value_none_fires_regardless_of_conf():
    """``amount_value=None`` fires ``amount_uncertain`` even at conf=1.0."""
    parts = _all_high_conf()
    parts.amount_value = None
    parts.amount_conf = 1.0
    assert derive_review_reasons(parts) == [ReviewReasonCode.amount_uncertain]


# ---------------------------------------------------------------------------
# Currency trigger
# ---------------------------------------------------------------------------


def test_unsupported_currency_fires():
    """``unsupported_currency=True`` fires its reason and nothing else."""
    parts = _all_high_conf()
    parts.unsupported_currency = True
    assert derive_review_reasons(parts) == [ReviewReasonCode.unsupported_currency]


def test_unsupported_currency_and_low_amount_conf_both_fire():
    """A non-AUD symbol typically tanks amount confidence — both fire.

    Canonical order places ``amount_uncertain`` before
    ``unsupported_currency`` per the enum declaration.
    """
    parts = _all_high_conf()
    parts.amount_conf = 0.3
    parts.unsupported_currency = True
    assert derive_review_reasons(parts) == [
        ReviewReasonCode.amount_uncertain,
        ReviewReasonCode.unsupported_currency,
    ]


# ---------------------------------------------------------------------------
# Job trigger
# ---------------------------------------------------------------------------


def test_job_conf_below_threshold():
    """``job_conf=0.69`` < 0.7 → job_uncertain."""
    parts = _all_high_conf()
    parts.job_conf = 0.69
    assert derive_review_reasons(parts) == [ReviewReasonCode.job_uncertain]


def test_job_conf_on_threshold_no_trigger():
    """``job_conf=0.7`` is NOT less than 0.7 → no trigger (boundary)."""
    parts = _all_high_conf()
    parts.job_conf = 0.7
    assert derive_review_reasons(parts) == []


def test_job_id_none_fires_regardless_of_conf():
    """``job_id=None`` fires job_uncertain regardless of conf."""
    parts = _all_high_conf()
    parts.job_id = None
    parts.job_conf = 1.0
    assert derive_review_reasons(parts) == [ReviewReasonCode.job_uncertain]


# ---------------------------------------------------------------------------
# Supplier trigger (expense_type-gated)
# ---------------------------------------------------------------------------


def test_supplier_conf_low_on_supplier_expense_fires():
    """Low ``supplier_conf`` on a supplier_expense row fires."""
    parts = _all_high_conf()
    parts.supplier_conf = 0.69
    parts.expense_type = ExpenseType.supplier_expense
    assert derive_review_reasons(parts) == [ReviewReasonCode.supplier_uncertain]


def test_supplier_conf_low_on_labour_does_not_fire():
    """Low ``supplier_conf`` on a labour row does NOT fire (gate)."""
    parts = _all_high_conf()
    parts.supplier_conf = 0.69
    parts.expense_type = ExpenseType.labour
    assert derive_review_reasons(parts) == []


def test_supplier_conf_low_on_adjustment_does_not_fire():
    """Low ``supplier_conf`` on an adjustment row does NOT fire (gate)."""
    parts = _all_high_conf()
    parts.supplier_conf = 0.69
    parts.expense_type = ExpenseType.adjustment
    assert derive_review_reasons(parts) == []


def test_supplier_conf_on_threshold_no_trigger():
    """``supplier_conf=0.7`` is NOT less than 0.7 → no trigger (boundary)."""
    parts = _all_high_conf()
    parts.supplier_conf = 0.7
    assert derive_review_reasons(parts) == []


# ---------------------------------------------------------------------------
# Category trigger
# ---------------------------------------------------------------------------


def test_category_conf_below_threshold():
    """``category_conf=0.59`` < 0.6 → category_uncertain."""
    parts = _all_high_conf()
    parts.category_conf = 0.59
    assert derive_review_reasons(parts) == [ReviewReasonCode.category_uncertain]


def test_category_conf_on_threshold_no_trigger():
    """``category_conf=0.6`` is NOT less than 0.6 → no trigger (boundary)."""
    parts = _all_high_conf()
    parts.category_conf = 0.6
    assert derive_review_reasons(parts) == []


# ---------------------------------------------------------------------------
# Duplicate trigger
# ---------------------------------------------------------------------------


def test_duplicate_flag_fires():
    """``duplicate_flag=True`` fires ``duplicate_suspected``."""
    parts = _all_high_conf()
    parts.duplicate_flag = True
    assert derive_review_reasons(parts) == [ReviewReasonCode.duplicate_suspected]


# ---------------------------------------------------------------------------
# All-at-once + canonical-order
# ---------------------------------------------------------------------------


def test_all_triggers_firing_returns_full_list_in_canonical_order():
    """All six triggers firing → all six reasons in enum-declaration order."""
    parts = ParsePartial(
        raw_text="garbled",
        amount_value=None,
        amount_conf=0.1,
        unsupported_currency=True,
        job_id=None,
        job_conf=0.1,
        supplier_id=None,
        supplier_conf=0.1,
        candidate_supplier_name=None,
        category_id=None,
        category_conf=0.1,
        payment_method=PaymentMethod.unknown,
        expense_type=ExpenseType.supplier_expense,
        description=None,
        duplicate_flag=True,
        duplicate_of_expense_id=uuid.uuid4(),
        source_per_field={},
    )
    assert derive_review_reasons(parts) == CANONICAL_ORDER


def test_three_triggers_canonical_order():
    """Three specific triggers (amount + job + category) → list in enum order.

    Even though amount is triggered last in the trigger table of the
    plan, the canonical output order places ``job_uncertain`` first
    and ``amount_uncertain`` after ``category_uncertain`` per the
    :class:`ReviewReasonCode` declaration. This test pins that order.
    """
    parts = _all_high_conf()
    parts.amount_conf = 0.5
    parts.job_id = None
    parts.category_conf = 0.2

    assert derive_review_reasons(parts) == [
        ReviewReasonCode.job_uncertain,
        ReviewReasonCode.category_uncertain,
        ReviewReasonCode.amount_uncertain,
    ]


# ---------------------------------------------------------------------------
# Payment-method has no effect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [PaymentMethod.cash, PaymentMethod.transfer, PaymentMethod.unknown],
)
def test_payment_method_does_not_drive_triggers(method: PaymentMethod):
    """No ``PaymentMethod`` variation changes the output vs. the high-conf baseline."""
    parts = _all_high_conf()
    parts.payment_method = method
    assert derive_review_reasons(parts) == []


def test_payment_method_change_does_not_add_reasons_when_others_fire():
    """Varying payment method doesn't add a reason on top of an existing trigger set."""
    base = _all_high_conf()
    base.amount_conf = 0.5
    base.payment_method = PaymentMethod.cash
    cash_reasons = derive_review_reasons(base)

    base.payment_method = PaymentMethod.transfer
    transfer_reasons = derive_review_reasons(base)

    assert cash_reasons == transfer_reasons == [ReviewReasonCode.amount_uncertain]


# ---------------------------------------------------------------------------
# Purity + determinism
# ---------------------------------------------------------------------------


def test_derive_is_pure_and_deterministic():
    """Calling ``derive`` twice on the same ``ParsePartial`` yields equal lists,
    and the input is unchanged.
    """
    parts = _all_high_conf()
    parts.amount_conf = 0.5
    parts.job_conf = 0.5
    parts.duplicate_flag = True

    # Deep-copy the underlying state (``ParsePartial`` is a mutable
    # dataclass; the purity assertion is that ``derive`` does not
    # alter any field on the passed instance).
    before = copy.deepcopy(asdict(parts))

    r1 = derive_review_reasons(parts)
    r2 = derive_review_reasons(parts)

    assert r1 == r2
    assert asdict(parts) == before


def test_returned_list_is_fresh_instance():
    """Each call returns a new list — callers can mutate it freely."""
    parts = _all_high_conf()
    parts.amount_conf = 0.5
    r1 = derive_review_reasons(parts)
    r2 = derive_review_reasons(parts)
    assert r1 is not r2
    r1.clear()
    assert r2 == [ReviewReasonCode.amount_uncertain]


def test_returned_list_elements_are_enum_members():
    """Every element of the result is a :class:`ReviewReasonCode` enum (not a string)."""
    parts = _all_high_conf()
    parts.amount_conf = 0.5
    parts.job_id = None
    parts.duplicate_flag = True
    parts.unsupported_currency = True

    for reason in derive_review_reasons(parts):
        assert isinstance(reason, ReviewReasonCode)


# ---------------------------------------------------------------------------
# A1b — money-integrity vs enrichment partition (routing)
# ---------------------------------------------------------------------------


def test_money_enrichment_partition_is_total_and_disjoint():
    """Every ReviewReasonCode is classified exactly once (money XOR enrichment).

    Guards A1b routing against a future reason code being added without
    being classified as money-integrity or enrichment.
    """
    assert MONEY_INTEGRITY_REASONS | ENRICHMENT_REASONS == set(ReviewReasonCode)
    assert MONEY_INTEGRITY_REASONS & ENRICHMENT_REASONS == set()


def test_money_integrity_membership():
    """The four amount/job-affecting reasons gate; the two label reasons do not."""
    assert MONEY_INTEGRITY_REASONS == {
        ReviewReasonCode.amount_uncertain,
        ReviewReasonCode.job_uncertain,
        ReviewReasonCode.duplicate_suspected,
        ReviewReasonCode.unsupported_currency,
    }
    assert ENRICHMENT_REASONS == {
        ReviewReasonCode.supplier_uncertain,
        ReviewReasonCode.category_uncertain,
    }


def test_gating_reasons_filters_enrichment_and_preserves_order():
    """gating_reasons keeps only money reasons, in the input (canonical) order."""
    # Supplier/category-only -> no gating reasons (the expense saves reviewed).
    assert (
        gating_reasons(
            [ReviewReasonCode.supplier_uncertain, ReviewReasonCode.category_uncertain]
        )
        == []
    )
    # Mixed -> money reasons only, original order preserved.
    assert gating_reasons(
        [
            ReviewReasonCode.job_uncertain,
            ReviewReasonCode.supplier_uncertain,
            ReviewReasonCode.category_uncertain,
            ReviewReasonCode.amount_uncertain,
        ]
    ) == [ReviewReasonCode.job_uncertain, ReviewReasonCode.amount_uncertain]
    # All-money -> unchanged.
    assert gating_reasons(
        [ReviewReasonCode.amount_uncertain, ReviewReasonCode.duplicate_suspected]
    ) == [ReviewReasonCode.amount_uncertain, ReviewReasonCode.duplicate_suspected]
