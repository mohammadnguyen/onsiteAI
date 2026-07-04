"""Loose date parser for capture-time expense_date input.

Accepts the date formats real users type on a phone keypad and
normalizes to a canonical :class:`datetime.date`. Backend-owned per
the P3 design decision: clients MAY pre-normalize for UX, but the
backend is the single source of truth for what an "expense_date"
string means once it crosses the API boundary.

Accepted shapes
---------------
* ISO:           ``YYYY-MM-DD`` (e.g. ``2026-05-22``) — fast path
* DD/MM:         ``22/05``     (year = today.year)
* DD-MM:         ``22-05``
* DD.MM:         ``22.05``
* D/M:           ``2/5``       (single-digit day or month allowed)
* DD/MM/YY:      ``22/05/26``  (YY normalized to ``20YY``)
* DD/MM/YYYY:    ``22/05/2026``
* The same shapes with ``-`` or ``.`` as the separator.

Policy
------
* **DD/MM only (AU)**. Inputs that happen to be valid in MM/DD
  convention (e.g. ``05/12``) are parsed as DD/MM (5 December).
* **Two-digit year normalises to 20YY**. ``26`` → ``2026``,
  ``00`` → ``2000``, ``99`` → ``2099``. No sliding-window logic.
* **Missing year defaults to ``today.year``**. No rollover heuristics
  — ``31/12`` typed on 1 January is interpreted as 31 December of
  the *current* year, not last year. If the user meant last year,
  they should type the year explicitly.
* **Mixed separators are rejected**. ``22/05-26`` raises.
* **Future dates are allowed** (P3). A future-date inline warning
  may be added in a later slice.
* **Invalid calendar dates** (``40/13``, ``29/02`` in a non-leap
  year, etc.) raise :class:`ValueError` with a descriptive message
  so Pydantic surfaces them as a clean 422.

The optional ``today`` parameter exists for test determinism: pass
``today=date(2026, 5, 22)`` so the year-defaulting behaviour is
reproducible regardless of when the test suite runs. At runtime
the default is :func:`datetime.date.today` (server local — UTC on
Fly).
"""

from __future__ import annotations

import re
from datetime import date, datetime

try:  # pragma: no cover - exercised only where the tz database is present
    from zoneinfo import ZoneInfo

    _SYDNEY: ZoneInfo | None = ZoneInfo("Australia/Sydney")
except Exception:  # ZoneInfoNotFoundError on a host without the tz database
    _SYDNEY = None


def _app_today() -> date:
    """Today's date in the app's operating timezone (Australia/Sydney).

    Year-less dates default their year from this. Using Sydney rather than the
    server's UTC clock avoids a new-year edge (audit C-5): a Sydney-morning
    capture on 1 Jan — when UTC is still 31 Dec — would otherwise default a
    year-less date to the previous year. Falls back to the server-local date
    when the IANA tz database is unavailable (e.g. a minimal host without
    ``tzdata``); on the Linux deploy target Sydney resolves normally.
    """
    if _SYDNEY is not None:
        return datetime.now(_SYDNEY).date()
    return date.today()


# ISO fast path: YYYY-MM-DD.
_ISO_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*$")

# Loose: DD<sep>MM[<sep>YY[YY]]  with separators in {/, -, .}.
# Back-reference ``(?P=sep)`` enforces a consistent separator across
# the whole string — ``22/05-26`` will not match.
_LOOSE_RE = re.compile(
    r"^\s*"
    r"(?P<day>\d{1,2})"
    r"(?P<sep>[/\-.])"
    r"(?P<month>\d{1,2})"
    r"(?:(?P=sep)(?P<year>\d{2}|\d{4}))?"
    r"\s*$"
)


def parse_loose_date(s: str, *, today: date | None = None) -> date:
    """Parse a loose user-typed date string into a canonical :class:`date`.

    See the module docstring for accepted formats and policy.

    Parameters
    ----------
    s : str
        The candidate date string. Surrounding whitespace is tolerated.
    today : date, optional
        Reference date for year-defaulting when the input omits the
        year. Defaults to :func:`date.today` at call time. Inject in
        tests for determinism.

    Returns
    -------
    date
        The parsed canonical date.

    Raises
    ------
    ValueError
        If ``s`` is not a string, is empty/whitespace-only, doesn't
        match any accepted shape, or denotes an invalid calendar date.
    """
    if not isinstance(s, str):
        raise ValueError(
            f"expected string, got {type(s).__name__}"
        )
    if not s.strip():
        raise ValueError("empty date string")

    iso_match = _ISO_RE.match(s)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError as exc:
            raise ValueError(f"invalid ISO date: {s!r}") from exc

    loose_match = _LOOSE_RE.match(s)
    if not loose_match:
        raise ValueError(f"unrecognized date format: {s!r}")

    day = int(loose_match.group("day"))
    month = int(loose_match.group("month"))
    year_str = loose_match.group("year")

    if year_str is None:
        year = (today or _app_today()).year
    elif len(year_str) == 2:
        year = 2000 + int(year_str)
    else:
        year = int(year_str)

    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(
            f"invalid calendar date: day={day} month={month} year={year}"
        ) from exc
