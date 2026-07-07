"""App-wide time helpers.

The product operates on Australian builder time. ``app_today()`` returns
the current date in Australia/Sydney rather than the server's UTC clock so
date defaults and past/future validation don't drift by a day around
midnight/new-year (audit C-5 / R32). Falls back to the server-local date
when the IANA tz database is unavailable (e.g. a minimal host without
``tzdata``); on the Linux deploy target Sydney resolves normally.
"""

from __future__ import annotations

from datetime import date, datetime

try:  # pragma: no cover - exercised only where the tz database is present
    from zoneinfo import ZoneInfo

    _SYDNEY: ZoneInfo | None = ZoneInfo("Australia/Sydney")
except Exception:  # ZoneInfoNotFoundError on a host without the tz database
    _SYDNEY = None


def app_today() -> date:
    """Today's date in the app's operating timezone (Australia/Sydney)."""
    if _SYDNEY is not None:
        return datetime.now(_SYDNEY).date()
    return date.today()
