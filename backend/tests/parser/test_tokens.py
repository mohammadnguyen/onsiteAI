"""Phase 2 Task T-E: unit tests for the parser tokenizer.

Pure-Python tests — no DB, no network. Covers:

* empty / whitespace-only input
* single ASCII + single CJK tokens
* bare numbers + currency+number pairs (AUD and unsupported)
* comma-grouped numbers normalise to digit-only form
* full-width NFKC folding flows through ``normalize_alias``
* span correctness into the original raw string
* mixed-language input
* degenerate cases (bare ``$``, ``123abc``, newlines/tabs)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services.parser.tokens import Token, tokenize


def test_token_is_frozen_and_hashable():
    """:class:`Token` is ``frozen=True`` — immutable and hashable."""
    t = Token(
        text="x",
        normalized="x",
        is_currency_symbol=False,
        is_numeric_like=False,
        span=(0, 1),
    )
    # hashable
    assert hash(t) == hash(t)
    # immutable — frozen dataclass raises FrozenInstanceError on assignment
    with pytest.raises(FrozenInstanceError):
        t.text = "y"  # type: ignore[misc]


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "\t\t", "\n\n", " \t \n "],
)
def test_empty_or_whitespace_returns_empty_list(raw: str):
    """Empty / whitespace-only input → ``[]``."""
    assert tokenize(raw) == []


def test_single_ascii_word():
    """Single ASCII word → one non-currency non-numeric token."""
    tokens = tokenize("Bunnings")
    assert len(tokens) == 1
    assert tokens[0].text == "Bunnings"
    assert tokens[0].normalized == "bunnings"
    assert tokens[0].is_currency_symbol is False
    assert tokens[0].is_numeric_like is False
    assert tokens[0].span == (0, 8)


def test_single_cjk_word():
    """Single CJK word → one token with NFKC-preserved normalized form."""
    tokens = tokenize("工地1")
    assert len(tokens) == 1
    assert tokens[0].text == "工地1"
    assert tokens[0].normalized == "工地1"
    assert tokens[0].is_currency_symbol is False
    assert tokens[0].is_numeric_like is False


def test_bare_number():
    """A bare number → one numeric-like token."""
    tokens = tokenize("305")
    assert len(tokens) == 1
    assert tokens[0].text == "305"
    assert tokens[0].normalized == "305"
    assert tokens[0].is_currency_symbol is False
    assert tokens[0].is_numeric_like is True


def test_dollar_and_integer():
    """``"$305"`` → two tokens: currency then numeric."""
    tokens = tokenize("$305")
    assert len(tokens) == 2
    assert tokens[0].text == "$"
    assert tokens[0].normalized == ""
    assert tokens[0].is_currency_symbol is True
    assert tokens[0].is_numeric_like is False
    assert tokens[0].span == (0, 1)
    assert tokens[1].text == "305"
    assert tokens[1].normalized == "305"
    assert tokens[1].is_currency_symbol is False
    assert tokens[1].is_numeric_like is True
    assert tokens[1].span == (1, 4)


def test_dollar_and_decimal():
    """``"$305.00"`` → two tokens: currency then decimal numeric."""
    tokens = tokenize("$305.00")
    assert len(tokens) == 2
    assert tokens[0].is_currency_symbol is True
    assert tokens[1].text == "305.00"
    assert tokens[1].normalized == "305.00"
    assert tokens[1].is_numeric_like is True


def test_dollar_and_comma_grouped_decimal():
    """``"$1,234.56"`` → currency + numeric with commas stripped on normalize."""
    tokens = tokenize("$1,234.56")
    assert len(tokens) == 2
    assert tokens[0].is_currency_symbol is True
    assert tokens[1].text == "1,234.56"
    assert tokens[1].normalized == "1234.56"
    assert tokens[1].is_numeric_like is True


def test_yen_is_recognised_as_currency_symbol():
    """``¥`` peels as a currency symbol (NOT AUD — unsupported)."""
    tokens = tokenize("¥163")
    assert len(tokens) == 2
    assert tokens[0].text == "¥"
    assert tokens[0].is_currency_symbol is True
    assert tokens[1].text == "163"
    assert tokens[1].normalized == "163"
    assert tokens[1].is_numeric_like is True


def test_euro_with_trailing_word():
    """``"€50 cash"`` → three tokens: €, 50, cash."""
    tokens = tokenize("€50 cash")
    assert len(tokens) == 3
    assert tokens[0].text == "€"
    assert tokens[0].is_currency_symbol is True
    assert tokens[1].text == "50"
    assert tokens[1].is_numeric_like is True
    assert tokens[2].text == "cash"
    assert tokens[2].normalized == "cash"


def test_pound_and_won_and_rupee_are_recognised():
    """£, ₩, ₹ are all in the recognised currency set."""
    for symbol in ["£", "₩", "₹"]:
        tokens = tokenize(f"{symbol}100")
        assert len(tokens) == 2, f"failed for {symbol!r}"
        assert tokens[0].text == symbol
        assert tokens[0].is_currency_symbol is True
        assert tokens[1].text == "100"
        assert tokens[1].is_numeric_like is True


def test_full_expense_string():
    """``"$305 Bunnings Kelly bluemetal"`` → 5 tokens in order."""
    tokens = tokenize("$305 Bunnings Kelly bluemetal")
    assert [t.text for t in tokens] == [
        "$",
        "305",
        "Bunnings",
        "Kelly",
        "bluemetal",
    ]
    assert tokens[0].is_currency_symbol is True
    assert tokens[1].is_numeric_like is True
    assert tokens[2].normalized == "bunnings"
    assert tokens[3].normalized == "kelly"
    assert tokens[4].normalized == "bluemetal"


def test_cjk_full_string():
    """``"工地1 水工材料 163"`` → 3 tokens; last is numeric."""
    tokens = tokenize("工地1 水工材料 163")
    assert len(tokens) == 3
    assert tokens[0].text == "工地1"
    assert tokens[1].text == "水工材料"
    assert tokens[1].normalized == "水工材料"
    assert tokens[2].text == "163"
    assert tokens[2].is_numeric_like is True


def test_full_width_nfkc_folding():
    """Full-width ``Ｓｉｔｅ１`` normalizes to ``site1`` via normalize_alias."""
    tokens = tokenize("Ｓｉｔｅ１")
    assert len(tokens) == 1
    assert tokens[0].text == "Ｓｉｔｅ１"  # original preserved
    assert tokens[0].normalized == "site1"  # NFKC + casefold
    assert tokens[0].is_numeric_like is False


def test_leading_and_trailing_whitespace_stripped():
    """Surrounding whitespace does not produce empty tokens."""
    tokens = tokenize("   Bunnings   ")
    assert len(tokens) == 1
    assert tokens[0].text == "Bunnings"
    # span reflects the trimmed position in the original string
    assert tokens[0].span == (3, 11)


def test_multiple_internal_whitespace_collapsed():
    """Runs of internal whitespace are treated as a single separator."""
    tokens = tokenize("a   b\t\tc")
    assert [t.text for t in tokens] == ["a", "b", "c"]


def test_mixed_language():
    """Mixed-language ``"Kelly 家 timber"`` → 3 tokens; middle is CJK."""
    tokens = tokenize("Kelly 家 timber")
    assert len(tokens) == 3
    assert tokens[0].text == "Kelly"
    assert tokens[1].text == "家"
    assert tokens[1].normalized == "家"
    assert tokens[2].text == "timber"


def test_digits_with_letters_is_not_numeric():
    """``"123abc"`` contains letters → not numeric-like."""
    tokens = tokenize("123abc")
    assert len(tokens) == 1
    assert tokens[0].text == "123abc"
    assert tokens[0].is_numeric_like is False
    assert tokens[0].normalized == "123abc"


def test_span_correctness_with_currency_peel():
    """Spans track original offsets — including peeled currency symbols."""
    tokens = tokenize("$305 Bunnings")
    assert tokens[0].span == (0, 1)  # "$"
    assert tokens[1].span == (1, 4)  # "305"
    assert tokens[2].span == (5, 13)  # "Bunnings"


def test_bare_currency_symbol_alone():
    """Bare ``$`` with nothing after → single currency-symbol token."""
    tokens = tokenize("$")
    assert len(tokens) == 1
    assert tokens[0].text == "$"
    assert tokens[0].is_currency_symbol is True
    assert tokens[0].is_numeric_like is False
    assert tokens[0].span == (0, 1)


def test_newlines_and_tabs_are_whitespace():
    """Newlines and tabs split just like spaces."""
    tokens = tokenize("a\nb\tc")
    assert [t.text for t in tokens] == ["a", "b", "c"]


def test_surrounding_and_internal_whitespace_combined():
    """``"  $305   Bunnings  "`` → 3 tokens with span offsets preserved."""
    tokens = tokenize("  $305   Bunnings  ")
    assert len(tokens) == 3
    assert [t.text for t in tokens] == ["$", "305", "Bunnings"]
    assert tokens[0].span == (2, 3)
    assert tokens[1].span == (3, 6)
    assert tokens[2].span == (9, 17)


def test_comma_grouped_without_currency():
    """``"1,234"`` on its own is still numeric-like, commas stripped."""
    tokens = tokenize("1,234")
    assert len(tokens) == 1
    assert tokens[0].text == "1,234"
    assert tokens[0].is_numeric_like is True
    assert tokens[0].normalized == "1234"


def test_comma_without_proper_grouping_is_not_numeric():
    """``"12,34"`` (wrong grouping) is not numeric-like."""
    tokens = tokenize("12,34")
    assert len(tokens) == 1
    assert tokens[0].is_numeric_like is False


def test_decimal_only_no_leading_digit_is_not_numeric():
    """``".5"`` alone is not numeric-like (regex requires leading digit)."""
    tokens = tokenize(".5")
    assert len(tokens) == 1
    assert tokens[0].is_numeric_like is False


def test_currency_symbol_peeled_mid_token_ascii():
    """``"abc$305"`` — the ``$`` IS now split out, even mid-chunk.

    Pre-fix behaviour: one weird token ``"abc$305"`` that neither the
    amount nor job matcher could handle. Post-fix: three clean tokens
    (word + currency + number) so downstream stages parse correctly.
    """
    tokens = tokenize("abc$305")
    assert [tok.text for tok in tokens] == ["abc", "$", "305"]
    assert [tok.is_currency_symbol for tok in tokens] == [False, True, False]
    assert [tok.is_numeric_like for tok in tokens] == [False, False, True]
    # Spans into the original "abc$305" string.
    assert [tok.span for tok in tokens] == [(0, 3), (3, 4), (4, 7)]


def test_currency_symbol_peeled_mid_token_cjk():
    """``"电工$100"`` — operator-reported gap. Mid-chunk ``$`` splits cleanly.

    Without this, the operator's natural typing pattern (CJK word
    immediately followed by a $-prefixed amount, no space between
    them) lost the amount entirely — amount stage saw no numeric-like
    candidate and the expense save returned "Amount is required".
    """
    tokens = tokenize("电工$100")
    assert [tok.text for tok in tokens] == ["电工", "$", "100"]
    assert [tok.is_currency_symbol for tok in tokens] == [False, True, False]
    assert [tok.is_numeric_like for tok in tokens] == [False, False, True]
    assert [tok.span for tok in tokens] == [(0, 2), (2, 3), (3, 6)]


def test_currency_symbol_peeled_trailing():
    """``"305$"`` — trailing ``$`` becomes its own token after the number."""
    tokens = tokenize("305$")
    assert [tok.text for tok in tokens] == ["305", "$"]
    assert [tok.is_currency_symbol for tok in tokens] == [False, True]
    assert [tok.is_numeric_like for tok in tokens] == [True, False]
    assert [tok.span for tok in tokens] == [(0, 3), (3, 4)]


def test_currency_symbol_peeled_multiple_in_chunk_limitation():
    """``"电工$100水工$200"`` — currency-only split (documented limitation).

    Only currency symbols are token boundaries; digit/letter
    boundaries inside a chunk do NOT split. So the middle "100水工"
    fragment stays as ONE token (mixed digits + CJK, NOT
    numeric_like). This means the SECOND amount wins (last numeric
    candidate) and the first $100 is effectively lost from amount
    extraction.

    This is acceptable because: (a) operator's multi-item workflow
    uses newlines between items (whitespace splits first), so this
    edge case doesn't fire in practice; (b) adding a digit/letter
    split would also break legitimate alphanumeric job codes like
    ``"KH-01"``. Test pins the limitation so a future "improvement"
    that breaks job codes is caught immediately.
    """
    tokens = tokenize("电工$100水工$200")
    assert [tok.text for tok in tokens] == [
        "电工",
        "$",
        "100水工",
        "$",
        "200",
    ]
    # "100水工" is NOT numeric_like (mixed letters), so amount stage
    # treats only the trailing "200" as a candidate.
    assert [tok.is_numeric_like for tok in tokens] == [
        False,
        False,
        False,
        False,
        True,
    ]


def test_alphanumeric_job_code_not_split_by_letter_digit_boundary():
    """Job codes like ``"KH-01"`` must stay as a single token.

    Pins the negative of the limitation above: we deliberately do
    NOT split on digit/letter boundaries. ``"KH-01"`` is a single
    word-ish token that the job matcher will resolve via the code
    route (not chopped into ``["KH", "-", "01"]`` or similar).
    """
    tokens = tokenize("KH-01")
    assert len(tokens) == 1
    assert tokens[0].text == "KH-01"
    assert tokens[0].is_currency_symbol is False
    assert tokens[0].is_numeric_like is False


def test_token_equality():
    """Two tokens built from the same chunk compare equal and hash equal."""
    a = tokenize("Bunnings")[0]
    b = tokenize("Bunnings")[0]
    assert a == b
    assert hash(a) == hash(b)
