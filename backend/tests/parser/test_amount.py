"""Phase 2 Task T-F: unit tests for the amount extractor.

Pure-Python tests — no DB, no network. Covers the full confidence
ladder, non-AUD currency handling, ambiguity ties, and the Phase 2
spec anchor inputs.

The test fixtures run raw text through the real tokenizer (T-E) so the
extractor is always exercised against the actual token contract, not a
hand-built token list. A few dedicated tests assert purity (input list
not mutated) and the no-ParsePartial-touching contract separately.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.parser.amount import AmountMatch, extract_amount
from app.services.parser.tokens import Token, tokenize

# ---------------------------------------------------------------------------
# Parametrized sweep — at least 30 cases covering every rule in the spec.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_text", "expected_value", "expected_conf", "expected_unsupported", "expected_ambiguous"),
    [
        # --- AUD integer (0.9) ---
        ("$305", Decimal("305"), 0.9, False, False),
        ("$1000", Decimal("1000"), 0.9, False, False),
        ("$0", Decimal("0"), 0.9, False, False),
        ("$10000000", Decimal("10000000"), 0.9, False, False),
        # --- AUD decimal (1.0) ---
        ("$305.00", Decimal("305.00"), 1.0, False, False),
        ("$305.50", Decimal("305.50"), 1.0, False, False),
        ("$12.50", Decimal("12.50"), 1.0, False, False),
        ("$1,234.56", Decimal("1234.56"), 1.0, False, False),
        # --- Bare integer (0.5) ---
        ("305", Decimal("305"), 0.5, False, False),
        ("42", Decimal("42"), 0.5, False, False),
        # --- Bare decimal (0.7) ---
        ("305.00", Decimal("305.00"), 0.7, False, False),
        ("42.99", Decimal("42.99"), 0.7, False, False),
        ("1,234.56", Decimal("1234.56"), 0.7, False, False),
        # --- Non-AUD prefix (0.3, unsupported=True) — all 5 symbols ---
        ("¥163", Decimal("163"), 0.3, True, False),
        ("€50", Decimal("50"), 0.3, True, False),
        ("£120.50", Decimal("120.50"), 0.3, True, False),
        ("₩10000", Decimal("10000"), 0.3, True, False),
        ("₹500", Decimal("500"), 0.3, True, False),
        # --- Clear winners from mixed inputs ---
        # $-prefix beats bare
        ("305 $50", Decimal("50"), 0.9, False, False),
        # $-prefix beats non-AUD: flag is False on the winner
        ("¥100 $50", Decimal("50"), 0.9, False, False),
        # Non-AUD beats nothing else: flag stays True on the winner
        ("¥100 timber", Decimal("100"), 0.3, True, False),
        # AUD decimal (1.0) beats AUD integer (0.9) from same input
        ("$305 $50.00", Decimal("50.00"), 1.0, False, False),
        # Bare decimal (0.7) beats bare integer (0.5)
        ("305 400.50", Decimal("400.50"), 0.7, False, False),
        # --- Ambiguous ties — confidence collapses to 0.0 ---
        # Two tied bare integers: value = first, ambiguous=True, conf=0.0
        ("305 400", Decimal("305"), 0.0, False, True),
        # Three-way tie
        ("100 200 300", Decimal("100"), 0.0, False, True),
        # Tie between two $-prefixed integers (both 0.9)
        ("$50 $60", Decimal("50"), 0.0, False, True),
        # Tie between two non-AUD: unsupported flag preserved on winner
        ("¥100 €200", Decimal("100"), 0.0, True, True),
        # --- Spec anchors ---
        ("$305 Bunnings Kelly bluemetal", Decimal("305"), 0.9, False, False),
        ("工地1 水工材料 163", Decimal("163"), 0.5, False, False),
        # --- No candidates / degenerate inputs ---
        ("Bunnings Kelly", None, 0.0, False, False),
        ("", None, 0.0, False, False),
        ("   ", None, 0.0, False, False),
        # Only a currency symbol — no numeric token at all
        ("$", None, 0.0, False, False),
        ("¥", None, 0.0, False, False),
        # Negative/parenthesised accounting notation is NOT supported in
        # Phase 2; tokenizer's numeric regex excludes them.
        ("-50", None, 0.0, False, False),
        ("(50)", None, 0.0, False, False),
        # --- Positional edge cases ---
        # Amount in the middle of a phrase
        ("paid $305 for timber", Decimal("305"), 0.9, False, False),
        # Leading whitespace
        ("  $305", Decimal("305"), 0.9, False, False),
        # Trailing whitespace
        ("$305   ", Decimal("305"), 0.9, False, False),
        # --- Multiple currency prefixes — immediate predecessor wins ---
        # Tokenizer peels "$¥305" into [$, ¥, 305]; the predecessor of
        # 305 is ¥, so it's treated as non-AUD unsupported.
        ("$¥305", Decimal("305"), 0.3, True, False),
        # Mirror: "¥$305" → [¥, $, 305], predecessor is $, AUD integer.
        ("¥$305", Decimal("305"), 0.9, False, False),
        # --- Full-width digits ---
        # "$１００" — tokenizer's numeric regex matches full-width digits
        # (Python's \d is Unicode-aware) so this IS a candidate; the
        # leading $ makes it an AUD integer. Decimal() parses full-width
        # digits too (via its own Unicode handling).
        ("$１００", Decimal("100"), 0.9, False, False),
        # "$Ｓｉｔｅ１" — the mixed-letters chunk is NOT numeric (letters
        # disqualify the regex), so no candidate at all.
        ("$Ｓｉｔｅ１", None, 0.0, False, False),
    ],
)
def test_extract_amount_matrix(
    raw_text: str,
    expected_value: Decimal | None,
    expected_conf: float,
    expected_unsupported: bool,
    expected_ambiguous: bool,
) -> None:
    """Drive the extractor end-to-end via the real tokenizer.

    Each row of the parametrize is one data point on the confidence
    ladder or an edge case called out in the plan. We tokenize the raw
    string, run the extractor, and check the four result fields.
    ``source_span`` is asserted separately in dedicated tests below.
    """
    tokens = tokenize(raw_text)
    result = extract_amount(tokens)
    assert result.value == expected_value, f"value mismatch for {raw_text!r}"
    assert result.confidence == expected_conf, f"confidence mismatch for {raw_text!r}"
    msg_unsupported = f"unsupported flag mismatch for {raw_text!r}"
    assert result.unsupported_currency == expected_unsupported, msg_unsupported
    assert result.ambiguous == expected_ambiguous, f"ambiguous flag mismatch for {raw_text!r}"


# ---------------------------------------------------------------------------
# Dedicated tests for contract / invariants not easily encoded above.
# ---------------------------------------------------------------------------


def test_amountmatch_is_frozen_and_hashable() -> None:
    """:class:`AmountMatch` is ``frozen=True`` — immutable and hashable."""
    m = AmountMatch(
        value=Decimal("1"),
        confidence=0.5,
        unsupported_currency=False,
        source_span=(0, 1),
        ambiguous=False,
    )
    assert hash(m) == hash(m)
    with pytest.raises(FrozenInstanceError):
        m.confidence = 0.9  # type: ignore[misc]


def test_empty_token_list_returns_no_match() -> None:
    """Explicit empty list (not via tokenize) also returns the zero match."""
    result = extract_amount([])
    assert result == AmountMatch(
        value=None,
        confidence=0.0,
        unsupported_currency=False,
        source_span=None,
        ambiguous=False,
    )


def test_source_span_points_at_the_winning_numeric_token() -> None:
    """``source_span`` matches the ``span`` of the chosen numeric token."""
    raw = "paid $305 for timber"
    tokens = tokenize(raw)
    result = extract_amount(tokens)
    # Find the "305" token and assert its span is what the match returns.
    numeric = next(t for t in tokens if t.text == "305")
    assert result.source_span == numeric.span
    assert raw[result.source_span[0] : result.source_span[1]] == "305"


def test_source_span_is_none_when_no_candidate() -> None:
    """When there is no candidate, ``source_span`` is ``None``."""
    result = extract_amount(tokenize("Bunnings Kelly"))
    assert result.source_span is None


def test_extractor_does_not_mutate_input_list() -> None:
    """Pure function — does not reorder/shrink/grow the input list."""
    tokens = tokenize("$305 Bunnings")
    before = list(tokens)
    extract_amount(tokens)
    assert tokens == before
    # And token instances are frozen — cannot be mutated anyway, but
    # explicitly assert identity preservation as a regression guard.
    assert all(a is b for a, b in zip(before, tokens, strict=True))


def test_extractor_is_deterministic_across_calls() -> None:
    """Calling twice with the same input returns equal results."""
    tokens = tokenize("$305 Bunnings Kelly bluemetal")
    assert extract_amount(tokens) == extract_amount(tokens)


def test_does_not_touch_parsepartial() -> None:
    """The extractor's return type is :class:`AmountMatch`, not ParsePartial.

    This is a structural guard against future regressions that might
    try to "help" by constructing a ParsePartial inside the stage.
    """
    from app.services.parser.llm_adapter import ParsePartial

    result = extract_amount(tokenize("$305"))
    assert not isinstance(result, ParsePartial)
    assert isinstance(result, AmountMatch)


def test_unsupported_flag_survives_when_non_aud_is_the_only_candidate() -> None:
    """Non-AUD winner keeps ``unsupported_currency=True`` even with other non-numeric junk."""
    result = extract_amount(tokenize("¥100 timber planks for job"))
    assert result.value == Decimal("100")
    assert result.confidence == 0.3
    assert result.unsupported_currency is True
    assert result.ambiguous is False


def test_unsupported_flag_cleared_when_dollar_wins() -> None:
    """A higher-confidence ``$`` candidate strips the unsupported flag off the overall result."""
    result = extract_amount(tokenize("¥100 $50"))
    assert result.value == Decimal("50")
    assert result.confidence == 0.9
    assert result.unsupported_currency is False


def test_token_list_directly_constructed() -> None:
    """Hand-built token list (not via tokenize) is handled identically."""
    # Simulate "$305" without going through tokenize, to prove the
    # extractor consumes any valid list[Token].
    tokens = [
        Token(
            text="$",
            normalized="",
            is_currency_symbol=True,
            is_numeric_like=False,
            span=(0, 1),
        ),
        Token(
            text="305",
            normalized="305",
            is_currency_symbol=False,
            is_numeric_like=True,
            span=(1, 4),
        ),
    ]
    result = extract_amount(tokens)
    assert result.value == Decimal("305")
    assert result.confidence == 0.9
    assert result.unsupported_currency is False
    assert result.ambiguous is False
    assert result.source_span == (1, 4)
