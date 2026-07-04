"""Audit fixes for the CJK / mixed amount parser (findings C-1, C-2).

* C-1 — malformed CJK place-marker sequences must NOT produce a plausible
  value that would then skip the review queue at high confidence.
* C-2 — common bilingual mixed Arabic+CJK forms (``100元``, ``5千``, ``3万5``)
  should extract an amount instead of silently failing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.parser.cjk_amounts import cjk_to_decimal

# ---------------------------------------------------------------------------
# C-1 — malformed CJK is rejected (was: silently accepted as a plausible value)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "十十",     # repeated place marker (was 20)
        "百百",     # was 200
        "千千",     # was 2000
        "十百",     # ascending place (was 110)
        "百千",     # ascending place
        "一万千",   # bare place marker after 万 (was 11000)
        "三万千",   # bare place marker after 万 (was 31000)
        "十十块",   # + money suffix — the dangerous 0.9-confidence case
        "一万千块",
    ],
)
def test_malformed_cjk_rejected(text: str):
    value, _ = cjk_to_decimal(text)
    assert value is None, f"{text!r} should be rejected, got {value}"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("五百五", Decimal(550)),      # colloquial trailing shift still works
        ("一百零五", Decimal(105)),    # zero-skip still works
        ("三万五千", Decimal(35000)),  # valid 万 form still works
        ("一万零五百", Decimal(10500)),
        ("五百五十", Decimal(550)),    # descending places (百 then 十) still ok
    ],
)
def test_valid_cjk_still_parses(text: str, expected: Decimal):
    value, _ = cjk_to_decimal(text)
    assert value == expected


# ---------------------------------------------------------------------------
# C-2 — mixed Arabic+CJK forms now extract an amount
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected,suffix",
    [
        ("100元", Decimal(100), True),
        ("50块", Decimal(50), True),
        ("5千", Decimal(5000), False),
        ("3万", Decimal(30000), False),
        ("10万", Decimal(100000), False),
        ("3万5", Decimal(35000), False),     # single trailing digit shifts to 千
        ("3万5千", Decimal(35000), False),
        ("10万5千", Decimal(105000), False),
        ("2百", Decimal(200), False),
    ],
)
def test_mixed_arabic_cjk_parses(text: str, expected: Decimal, suffix: bool):
    value, had_suffix = cjk_to_decimal(text)
    assert value == expected
    assert had_suffix is suffix


@pytest.mark.parametrize(
    "text",
    [
        "3万50",   # ambiguous multi-digit tail without magnitude — not guessed
        "abc",
        "5.5千",   # decimals not handled by the integer mixed parser
    ],
)
def test_mixed_ambiguous_or_unhandled_returns_none(text: str):
    value, _ = cjk_to_decimal(text)
    assert value is None
