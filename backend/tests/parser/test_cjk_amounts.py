"""Capture Parser v1: unit tests for the CJK amount pre-extraction stage.

Pure-Python tests — no DB, no network. Covers:

* Positive cases for every supported CJK form (digits, places, money
  suffixes, colloquial shift, zero-skip).
* Negative cases for date words, site words, single bare digits,
  out-of-scope numerals, and pure non-CJK input.
* The token-rewriter contract (synthetic ``$`` insertion for money
  suffixes, span preservation, idempotency, input non-mutation).

End-to-end coverage through the full pipeline lives in
``test_amount.py`` (via ``tokenize`` → ``normalize_cjk_amount_tokens``
→ ``extract_amount``); this file exercises the new module in
isolation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.parser.cjk_amounts import (
    cjk_to_decimal,
    normalize_cjk_amount_tokens,
)
from app.services.parser.tokens import tokenize


# ---------------------------------------------------------------------------
# cjk_to_decimal — positive cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_suffix"),
    [
        # --- Bare CJK numerals (no money suffix) ---
        # Colloquial shift rule (trailing single digit goes one place
        # below the last seen place marker).
        ("五百五", Decimal(550), False),
        ("一百五", Decimal(150), False),
        ("一千二", Decimal(1200), False),
        ("两千三", Decimal(2300), False),  # 两 = 二 alternate digit
        ("三万二", Decimal(32000), False),  # shift after 万 → 千
        # Formal multi-place forms (every place explicit, no shift).
        ("一千二百三十", Decimal(1230), False),
        # Implicit-leading-1 + colloquial.
        ("十二", Decimal(12), False),
        ("十", Decimal(10), False),
        ("二十", Decimal(20), False),
        ("二十一", Decimal(21), False),
        # Single place markers.
        ("一百", Decimal(100), False),
        ("两百", Decimal(200), False),
        ("一千", Decimal(1000), False),
        ("一万", Decimal(10000), False),
        # Zero-skip rule (零 means skip a place; trailing digit lands in
        # ones place rather than getting the colloquial shift).
        ("一百零五", Decimal(105), False),
        ("五千零五", Decimal(5005), False),
        ("一万零五百", Decimal(10500), False),
        # Larger values still under the cap.
        ("一万二千", Decimal(12000), False),
        ("五千万", Decimal(50000000), False),
        ("一千万", Decimal(10000000), False),  # boundary of downstream cap
        # --- CJK with money suffix (had_suffix=True) ---
        ("五百块", Decimal(500), True),
        ("八百元", Decimal(800), True),
        ("五百澳币", Decimal(500), True),
        ("八百澳元", Decimal(800), True),
        ("一千二块", Decimal(1200), True),
        ("三万二元", Decimal(32000), True),
        # Single bare digit + suffix is allowed (suffix unambiguously
        # marks intent as money).
        ("五块", Decimal(5), True),
        ("十块", Decimal(10), True),
    ],
)
def test_cjk_to_decimal_positive(
    text: str, expected_value: Decimal, expected_suffix: bool
) -> None:
    """Every supported form parses to its expected Decimal + suffix flag."""
    value, had_suffix = cjk_to_decimal(text)
    assert value == expected_value, f"value mismatch for {text!r}"
    assert had_suffix is expected_suffix, f"suffix flag mismatch for {text!r}"


# ---------------------------------------------------------------------------
# cjk_to_decimal — negative cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # --- Single bare CJK digits (no place marker, no suffix) ---
        "五",
        "九",
        "一",
        "二",
        "零",
        "〇",
        "两",
        # --- Date words (contain chars outside the whitelist) ---
        "五月",
        "五月五号",
        "五日",
        "五号",
        "五年",
        "三月十五号",
        # --- Site / proper-noun words (mixed CJK + non-numeral chars) ---
        "工地一",
        "工地五",
        "晶晶家",
        "水泥",
        "千葉",  # Japanese place name "Chiba" — 葉 not in whitelist
        # --- Non-CJK input (should never reach the CJK parser, but
        # exercise it defensively) ---
        "Site1",
        "JJ-01",
        "Bunnings",
        "0400",
        "",
        "   ",
        # --- Out-of-scope numerals ---
        "亿",
        "壹貳參",  # traditional financial
        "五角",  # sub-yuan (角 = 0.1)
        "五分",  # sub-yuan (分 = 0.01)
        # --- Out-of-range / malformed ---
        "一万万",  # 亿-level (two 万 chars)
        # --- Suffix-only or zero-amount-with-suffix ---
        "块",
        "元",
        "澳币",
        "澳元",
        "零块",
        "零元",
        "〇块",
        # --- Multi-char digit-only with suffix (no place marker) ---
        "五五块",  # gate 2 fails inside _parse_cjk_numeral
        # --- Two consecutive digits without place marker ---
        "五五",
    ],
)
def test_cjk_to_decimal_negative(text: str) -> None:
    """Date words, site words, bare digits, and out-of-scope inputs reject."""
    value, _ = cjk_to_decimal(text)
    assert value is None, f"expected None for {text!r}, got {value}"


# ---------------------------------------------------------------------------
# normalize_cjk_amount_tokens — token-rewriter behaviour
# ---------------------------------------------------------------------------


def test_rewriter_passes_through_when_no_cjk_amount() -> None:
    """Plain ASCII / numeric / currency-only input is returned unchanged."""
    tokens = tokenize("Bunnings $305 cement")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert rewritten == tokens


def test_rewriter_replaces_bare_cjk_numeric_in_place() -> None:
    """``Bunnings 五百五 Smith`` → middle token rewritten to numeric '550'."""
    tokens = tokenize("Bunnings 五百五 Smith")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert len(rewritten) == 3
    assert rewritten[0].text == "Bunnings"
    assert rewritten[2].text == "Smith"
    middle = rewritten[1]
    assert middle.text == "五百五"  # original preserved for diagnostics
    assert middle.normalized == "550"  # Arabic value
    assert middle.is_numeric_like is True
    assert middle.is_currency_symbol is False
    # Span preserved
    original_middle = next(t for t in tokens if t.text == "五百五")
    assert middle.span == original_middle.span


def test_rewriter_emits_synthetic_dollar_for_money_suffix() -> None:
    """``Bunnings 五百块 Smith`` → 4 tokens, with synthetic '$' inserted."""
    tokens = tokenize("Bunnings 五百块 Smith")
    rewritten = normalize_cjk_amount_tokens(tokens)
    # Original: [Bunnings, 五百块, Smith] → 3 tokens.
    # Rewritten: [Bunnings, $, 五百块(numeric), Smith] → 4 tokens.
    assert len(rewritten) == 4
    assert rewritten[0].text == "Bunnings"
    assert rewritten[1].text == "$"
    assert rewritten[1].is_currency_symbol is True
    assert rewritten[1].is_numeric_like is False
    assert rewritten[2].text == "五百块"  # original preserved
    assert rewritten[2].normalized == "500"
    assert rewritten[2].is_numeric_like is True
    assert rewritten[3].text == "Smith"


def test_rewriter_synthetic_dollar_has_zero_width_span_at_chunk_start() -> None:
    """Synthetic ``$`` span is ``(start, start)``; numeric span is full chunk."""
    tokens = tokenize("Bunnings 五百块 Smith")
    rewritten = normalize_cjk_amount_tokens(tokens)
    original_chunk = next(t for t in tokens if t.text == "五百块")
    synthetic_dollar = rewritten[1]
    numeric = rewritten[2]
    assert synthetic_dollar.span == (original_chunk.span[0], original_chunk.span[0])
    assert numeric.span == original_chunk.span


def test_rewriter_skips_already_numeric() -> None:
    """Tokens with ``is_numeric_like=True`` are passed through untouched."""
    tokens = tokenize("Bunnings 305 cement")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert rewritten == tokens


def test_rewriter_skips_currency_symbol() -> None:
    """Tokens with ``is_currency_symbol=True`` are passed through untouched."""
    tokens = tokenize("$305")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert rewritten == tokens


def test_rewriter_does_not_mutate_input_list() -> None:
    """Pure function — input list is not reordered, shrunk, or grown."""
    tokens = tokenize("Bunnings 五百五 Smith")
    snapshot = list(tokens)
    normalize_cjk_amount_tokens(tokens)
    assert tokens == snapshot
    # Token instances are frozen; identity preservation as a regression
    # guard for accidental in-place rewriting.
    assert all(a is b for a, b in zip(snapshot, tokens, strict=True))


def test_rewriter_idempotent() -> None:
    """Calling twice produces the same output as calling once."""
    tokens = tokenize("Bunnings 五百块 一千二 Smith")
    once = normalize_cjk_amount_tokens(tokens)
    twice = normalize_cjk_amount_tokens(once)
    assert once == twice


def test_rewriter_passes_through_negative_cases() -> None:
    """Date / site words are not rewritten."""
    tokens = tokenize("五月五号 工地一 Bunnings")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert len(rewritten) == 3
    for r, original in zip(rewritten, tokens, strict=True):
        assert r == original


def test_rewriter_handles_empty_input() -> None:
    """Empty list returns empty list."""
    assert normalize_cjk_amount_tokens([]) == []


def test_rewriter_handles_mixed_arabic_and_cjk() -> None:
    """Arabic and CJK numerics in same input both reach the extractor."""
    tokens = tokenize("Bunnings $305 五百五 Smith")
    rewritten = normalize_cjk_amount_tokens(tokens)
    # Original: [Bunnings, $, 305, 五百五, Smith] → 5 tokens.
    # 五百五 has no money suffix → no synthetic $ injected. Result 5 tokens.
    assert len(rewritten) == 5
    cjk_token = next(t for t in rewritten if t.text == "五百五")
    assert cjk_token.is_numeric_like is True
    assert cjk_token.normalized == "550"
    # Arabic 305 stays as-is
    arabic_token = next(t for t in rewritten if t.text == "305")
    assert arabic_token.is_numeric_like is True


def test_rewriter_handles_two_cjk_amounts_in_same_input() -> None:
    """Multiple CJK amounts each get rewritten independently."""
    # 五百块 has suffix (synthetic $ injected); 一千二 is bare (no $).
    # Original: [Bunnings, 五百块, Reece, 一千二, Smith] → 5 tokens.
    # Rewritten: [Bunnings, $, 五百块(numeric), Reece, 一千二(numeric), Smith] → 6.
    tokens = tokenize("Bunnings 五百块 Reece 一千二 Smith")
    rewritten = normalize_cjk_amount_tokens(tokens)
    assert len(rewritten) == 6
    # Find the two numeric rewrites
    numerics = [t for t in rewritten if t.is_numeric_like]
    assert len(numerics) == 2
    assert {t.normalized for t in numerics} == {"500", "1200"}


def test_rewritten_token_decimal_normalization_round_trips_through_decimal() -> None:
    """The rewritten ``normalized`` string parses cleanly via ``Decimal()``.

    This is the contract the downstream amount extractor relies on:
    ``Decimal(numeric_token.normalized)`` MUST work for any rewritten
    CJK token.
    """
    for text in ("五百五", "一千二百三十", "三万二", "一万", "十"):
        tokens = tokenize(f"Bunnings {text} Smith")
        rewritten = normalize_cjk_amount_tokens(tokens)
        numeric = next(t for t in rewritten if t.is_numeric_like)
        # Should not raise, and value should match cjk_to_decimal output.
        parsed = Decimal(numeric.normalized)
        expected, _ = cjk_to_decimal(text)
        assert parsed == expected
