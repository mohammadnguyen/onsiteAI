"""Unit tests for the loose expense_date parser.

These tests are intentionally isolated from the rest of the API/service
layer per the P3 design: the parser is core capture infrastructure that
will be exercised through many paths (ExpenseCreate, ExpenseUpdate,
ParsePreviewRequest, future bulk-import etc.) so it deserves a
dedicated test surface.

The ``today`` argument is injected on every call so test outcomes are
deterministic regardless of when the suite runs.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.parser.dates import parse_loose_date

# Anchored reference date: 22 May 2026.
_TODAY = date(2026, 5, 22)


# ---------------------------------------------------------------------------
# Happy-path numeric formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # DD/MM with all three separators -> uses today.year
        ("22/05", date(2026, 5, 22)),
        ("22-05", date(2026, 5, 22)),
        ("22.05", date(2026, 5, 22)),
        # D/M (single-digit) -> uses today.year
        ("2/5", date(2026, 5, 2)),
        ("2-5", date(2026, 5, 2)),
        ("2.5", date(2026, 5, 2)),
        # DD/MM/YY -> 20YY
        ("22/05/26", date(2026, 5, 22)),
        ("22-05-26", date(2026, 5, 22)),
        ("22.05.26", date(2026, 5, 22)),
        # DD/MM/YYYY (explicit four-digit year)
        ("22/05/2026", date(2026, 5, 22)),
        ("22-05-2026", date(2026, 5, 22)),
        ("22.05.2026", date(2026, 5, 22)),
        # ISO fast path
        ("2026-05-22", date(2026, 5, 22)),
        # Single-digit day with two-digit year
        ("2/5/26", date(2026, 5, 2)),
        # Leap-year valid
        ("29/02/2024", date(2024, 2, 29)),
        ("29-02-24", date(2024, 2, 29)),
    ],
)
def test_parse_loose_date_happy_path(raw: str, expected: date) -> None:
    assert parse_loose_date(raw, today=_TODAY) == expected


# ---------------------------------------------------------------------------
# Whitespace + ISO edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  22/05  ", date(2026, 5, 22)),
        ("\t22/05\n", date(2026, 5, 22)),
        ("  2026-05-22  ", date(2026, 5, 22)),
    ],
)
def test_parse_loose_date_tolerates_whitespace(raw: str, expected: date) -> None:
    assert parse_loose_date(raw, today=_TODAY) == expected


# ---------------------------------------------------------------------------
# Two-digit year boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01/01/00", date(2000, 1, 1)),
        ("01/01/49", date(2049, 1, 1)),
        ("01/01/50", date(2050, 1, 1)),
        ("01/01/99", date(2099, 1, 1)),
    ],
)
def test_parse_loose_date_two_digit_year_always_20yy(
    raw: str, expected: date
) -> None:
    """Per PR-3: two-digit year always maps to 20YY (no sliding window)."""
    assert parse_loose_date(raw, today=_TODAY) == expected


# ---------------------------------------------------------------------------
# Missing year uses today.year (no rollover heuristics — PR-2 + PR-7)
# ---------------------------------------------------------------------------


def test_missing_year_uses_today_year() -> None:
    assert parse_loose_date("22/05", today=date(2026, 5, 22)) == date(2026, 5, 22)
    assert parse_loose_date("22/05", today=date(2030, 1, 1)) == date(2030, 5, 22)


def test_missing_year_no_rollover_logic() -> None:
    """A date that would be in the past by today's clock is NOT
    silently rolled to next year (and vice versa for future-feeling
    dates being rolled back). Per PR-7: no rollover."""
    # Today is 1 January 2026; user types "31/12" -> 31 Dec 2026
    # (a future date), NOT 31 Dec 2025.
    assert parse_loose_date("31/12", today=date(2026, 1, 1)) == date(2026, 12, 31)
    # Symmetric: today is 31 December 2026; user types "01/01"
    # -> 1 Jan 2026 (a past date), NOT 1 Jan 2027.
    assert parse_loose_date("01/01", today=date(2026, 12, 31)) == date(2026, 1, 1)


# ---------------------------------------------------------------------------
# Future dates allowed (PR-5)
# ---------------------------------------------------------------------------


def test_future_dates_allowed() -> None:
    """Per PR-5: P3 has no hard block on future dates."""
    far_future = parse_loose_date("01/01/2099", today=_TODAY)
    assert far_future == date(2099, 1, 1)


# ---------------------------------------------------------------------------
# Invalid calendar dates -> ValueError (PR-6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "40/05",  # day out of range
        "32/01",
        "00/05",  # day = 0
        "22/13",  # month out of range
        "22/00",  # month = 0
        "31/04",  # April has 30 days
        "29/02/2023",  # 2023 is not a leap year
        "31/02/2024",  # never valid
    ],
)
def test_invalid_calendar_dates_raise(raw: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_loose_date(raw, today=_TODAY)
    assert "invalid calendar date" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Malformed / unparseable input -> ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty string
        "   ",  # whitespace only
        "abc",  # not a date
        "22/may/2026",  # month-name not supported in P3
        "22 / 05 / 2026",  # inner spaces not supported
        "22/5/2026/extra",  # trailing junk
        "22//05",  # double separator
        "/22/05",  # leading separator
        "22/",  # incomplete
        "22/05/",  # trailing separator with no year
    ],
)
def test_malformed_input_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_loose_date(raw, today=_TODAY)


def test_mixed_separator_rejected() -> None:
    """Per the regex back-reference: separator must be consistent."""
    with pytest.raises(ValueError):
        parse_loose_date("22/05-26", today=_TODAY)
    with pytest.raises(ValueError):
        parse_loose_date("22-05.26", today=_TODAY)


# ---------------------------------------------------------------------------
# Type errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, 123, 22.5, [], {}, date(2026, 5, 22)])
def test_non_string_input_raises_type_error(bad: object) -> None:
    """The parser only accepts strings — the schema validator handles
    pre-existing :class:`date` objects with an early return before
    ever calling into the parser."""
    with pytest.raises(ValueError):
        parse_loose_date(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Smoke: default today resolves to date.today() at runtime
# ---------------------------------------------------------------------------


def test_today_default_runtime() -> None:
    """When ``today`` is omitted, the parser uses :func:`date.today`
    at call time."""
    expected_year = date.today().year
    assert parse_loose_date("22/05").year == expected_year
