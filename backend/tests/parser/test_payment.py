"""Phase 2 Task T-I: unit tests for the payment-method extractor.

Pure-Python tests — no DB, no network. Covers:

* empty input → unknown
* EN keywords: cash / transfer / eft / bank (casefold via tokenizer)
* zh keywords: 现金 / 转账 / 银行
* spec-anchor strings with no payment token → unknown
* first-match-wins across both cash+transfer and transfer+cash inputs
* "paid cash" pinning the "paid is not a keyword" decision
* purity (input list is not mutated)
* no confidence score / narrow dataclass — the function returns a bare
  :class:`PaymentMethod` enum value
"""

from __future__ import annotations

import pytest

from app.models import PaymentMethod
from app.services.parser.payment import extract_payment_method
from app.services.parser.tokens import tokenize


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        # --- Empty / whitespace-only → unknown ---
        ("", PaymentMethod.unknown),
        ("   ", PaymentMethod.unknown),
        # --- Plain keywords in isolation ---
        ("cash", PaymentMethod.cash),
        ("CASH", PaymentMethod.cash),  # casefold via tokenizer
        ("Cash", PaymentMethod.cash),
        ("现金", PaymentMethod.cash),
        ("transfer", PaymentMethod.transfer),
        ("TRANSFER", PaymentMethod.transfer),
        ("eft", PaymentMethod.transfer),
        ("EFT", PaymentMethod.transfer),
        ("bank", PaymentMethod.transfer),
        ("Bank", PaymentMethod.transfer),
        ("转账", PaymentMethod.transfer),
        ("银行", PaymentMethod.transfer),
        # --- Phase 2 spec anchor inputs with no payment token ---
        ("$305 Bunnings Kelly bluemetal", PaymentMethod.unknown),
        ("工地1 水工材料 163", PaymentMethod.unknown),
        # --- Keyword embedded in a realistic expense string ---
        ("$305 Bunnings cash", PaymentMethod.cash),
        ("eft 500 Bunnings", PaymentMethod.transfer),
        ("$50 bank fee", PaymentMethod.transfer),
        ("工地1 现金 163", PaymentMethod.cash),
        ("工地1 银行 500", PaymentMethod.transfer),
        # --- First-match-wins pair tests ---
        # "paid cash" — if "paid" were in the transfer set it would
        # pre-empt "cash" and return the wrong answer. This test pins
        # both the first-match-wins rule AND that "paid" is NOT a
        # transfer keyword.
        ("paid cash", PaymentMethod.cash),
        ("cash transfer", PaymentMethod.cash),
        ("transfer cash", PaymentMethod.transfer),
        ("bank cash", PaymentMethod.transfer),
        ("cash bank", PaymentMethod.cash),
        ("现金 转账", PaymentMethod.cash),
        ("转账 现金", PaymentMethod.transfer),
        # --- Non-payment words alone → unknown ---
        ("random words", PaymentMethod.unknown),
        ("Bunnings timber", PaymentMethod.unknown),
        # --- "paid" alone is NOT a payment keyword ---
        ("paid", PaymentMethod.unknown),
        ("paid the supplier", PaymentMethod.unknown),
    ],
)
def test_extract_payment_method(raw_text: str, expected: PaymentMethod):
    """Parametrized sweep: tokenize raw_text, expect ``expected`` payment method."""
    tokens = tokenize(raw_text)
    assert extract_payment_method(tokens) == expected


def test_currency_and_numeric_tokens_are_skipped():
    """Currency symbols and numeric tokens must not be probed as keywords.

    Constructed to exercise the skip branch with multiple $/numeric
    tokens surrounding the real keyword.
    """
    tokens = tokenize("$ 305 $1,234.56 cash 42")
    assert extract_payment_method(tokens) == PaymentMethod.cash


def test_returns_enum_not_string():
    """The function returns a :class:`PaymentMethod` enum, not a raw string."""
    tokens = tokenize("cash")
    result = extract_payment_method(tokens)
    assert isinstance(result, PaymentMethod)
    assert result is PaymentMethod.cash


def test_input_list_not_mutated():
    """Pure-function contract: the input ``tokens`` list is not mutated.

    Guards against accidental ``tokens.pop()`` / ``tokens.reverse()`` /
    etc. inside the extractor. Compares the list by both identity of
    elements AND length before/after the call.
    """
    tokens = tokenize("$305 Bunnings cash transfer")
    snapshot = list(tokens)
    original_len = len(tokens)

    extract_payment_method(tokens)

    assert len(tokens) == original_len
    # Element-wise identity check — frozen Tokens are hashable but we
    # want to pin that no token was replaced with an equal copy either.
    for before, after in zip(snapshot, tokens, strict=True):
        assert before is after


def test_empty_token_list_returns_unknown():
    """Empty list input (not just empty string) → unknown."""
    assert extract_payment_method([]) == PaymentMethod.unknown


def test_return_values_are_only_the_three_allowed_enum_members():
    """Only cash / transfer / unknown are ever returned.

    Sweeps a broad set of inputs and asserts the return is always one
    of the three allowed enum members — pins the contract that we
    never accidentally return a string, None, or some other enum.
    """
    allowed = {PaymentMethod.cash, PaymentMethod.transfer, PaymentMethod.unknown}
    samples = [
        "",
        "cash",
        "transfer",
        "eft",
        "bank",
        "现金",
        "转账",
        "银行",
        "random",
        "$305 Bunnings Kelly bluemetal",
        "paid cash",
        "cash transfer",
    ]
    for raw in samples:
        result = extract_payment_method(tokenize(raw))
        assert result in allowed, f"unexpected return {result!r} for {raw!r}"
