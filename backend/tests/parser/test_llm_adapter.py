"""Phase 2 Task T-D: unit tests for the LLMParser interface + MockLLMParser.

These are pure-Python tests — no DB fixture, no HTTP, no network. They
cover:

* the abstract interface cannot be instantiated directly
* the mock is a concrete subclass and passes ``isinstance``
* the mock returns ``rules_partial`` unchanged (same identity + equal)
* the mock is idempotent across repeated calls
* :class:`ParsePartial` has sensible defaults on every optional field
* the async signature is preserved on the mock
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from app.models import ExpenseType, PaymentMethod
from app.services.parser.llm_adapter import LLMParser, MockLLMParser, ParsePartial


def test_llm_parser_is_abstract():
    """:class:`LLMParser` is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        LLMParser()  # type: ignore[abstract]


def test_mock_respects_interface():
    """:class:`MockLLMParser` is a concrete subclass of :class:`LLMParser`."""
    assert isinstance(MockLLMParser(), LLMParser)


def test_mock_async_contract():
    """:meth:`MockLLMParser.parse` is an ``async def``; Phase 2.5 drops
    in a real async client without changing the seam."""
    assert asyncio.iscoroutinefunction(MockLLMParser.parse)


def test_parse_partial_defaults():
    """Only ``raw_text`` is required; every other field defaults sensibly."""
    partial = ParsePartial(raw_text="")

    assert partial.raw_text == ""
    assert partial.amount_value is None
    assert partial.amount_conf == 0.0
    assert partial.unsupported_currency is False
    assert partial.job_id is None
    assert partial.job_conf == 0.0
    assert partial.supplier_id is None
    assert partial.supplier_conf == 0.0
    assert partial.candidate_supplier_name is None
    assert partial.category_id is None
    assert partial.category_conf == 0.0
    assert partial.payment_method == PaymentMethod.unknown
    assert partial.expense_type == ExpenseType.supplier_expense
    assert partial.description is None
    assert partial.duplicate_flag is False
    assert partial.duplicate_of_expense_id is None
    assert partial.source_per_field == {}


@pytest.mark.asyncio
async def test_mock_returns_rules_partial_unchanged():
    """The mock returns the input partial untouched — same values and
    same ``source_per_field`` provenance."""
    supplier_id = uuid.uuid4()
    job_id = uuid.uuid4()
    partial = ParsePartial(
        raw_text="$305 Kelly",
        amount_value=Decimal("305.00"),
        amount_conf=0.95,
        job_id=job_id,
        job_conf=0.8,
        supplier_id=supplier_id,
        supplier_conf=0.7,
        payment_method=PaymentMethod.cash,
        source_per_field={"amount": "rules", "job": "rules", "supplier": "rules"},
    )

    result = await MockLLMParser().parse("$305 Kelly", partial)

    assert result is partial
    assert result.amount_value == Decimal("305.00")
    assert result.amount_conf == 0.95
    assert result.job_id == job_id
    assert result.supplier_id == supplier_id
    assert result.payment_method == PaymentMethod.cash
    assert result.source_per_field == {
        "amount": "rules",
        "job": "rules",
        "supplier": "rules",
    }


@pytest.mark.asyncio
async def test_mock_is_idempotent():
    """Repeated mock calls with the same input return the same object."""
    partial = ParsePartial(
        raw_text="$42 timber",
        amount_value=Decimal("42.00"),
        amount_conf=0.9,
        source_per_field={"amount": "rules"},
    )
    parser = MockLLMParser()

    first = await parser.parse("$42 timber", partial)
    second = await parser.parse("$42 timber", partial)

    assert first is partial
    assert second is partial
    assert first is second
