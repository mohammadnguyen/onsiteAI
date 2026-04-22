"""Unit tests for :func:`app.core.text.normalize_alias`.

These cases pin down the alias-normalisation invariants that make
``JobAlias`` rows uniquely keyable across English, Chinese, full-width,
and punctuation-spaced variants. Phase 2's expense parser will match
free-form input against the normalised form, so every case here is
load-bearing.
"""

import pytest

from app.core.text import normalize_alias


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Site1", "site1"),
        ("site 1", "site1"),
        ("SITE-1", "site1"),
        ("Ｓｉｔｅ１", "site1"),
        ("Kelly", "kelly"),
        ("kelly", "kelly"),
        ("Kelly.", "kelly"),
        ("Kelly House", "kellyhouse"),
        ("kelly-house", "kellyhouse"),
        ("Kelly_House", "kellyhouse"),
        ("工地1", "工地1"),
        ("工地 1", "工地1"),
        ("工地１", "工地1"),
        ("Kelly 家", "kelly家"),
        ("kelly家", "kelly家"),
        ("Kelly_家", "kelly家"),
        ("  Kelly  ", "kelly"),
    ],
)
def test_normalize_alias(raw: str, expected: str) -> None:
    assert normalize_alias(raw) == expected
