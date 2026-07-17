/**
 * Date helpers for the mobile capture + detail screens.
 *
 * Mobile-side parsing is UX-only: the backend's
 * ``app.services.parser.dates.parse_loose_date`` remains the single
 * source of truth for what an expense_date string means once it
 * crosses the API boundary. The parser here mirrors the backend's
 * accepted shapes + policy so the same input feels consistent at
 * type-time — but returns ``null`` on failure (the caller decides how
 * to surface the error) instead of raising, because RN UI code should
 * never throw out of a keystroke handler.
 *
 * Accepted shapes (mirror of backend)
 * ------------------------------------
 *  - ISO:           YYYY-MM-DD (fast path)
 *  - DD/MM:         22/05      (year = today.year)
 *  - DD-MM:         22-05
 *  - DD.MM:         22.05
 *  - D/M:           2/5
 *  - DD/MM/YY:      22/05/26   (YY -> 20YY)
 *  - DD/MM/YYYY:    22/05/2026
 *
 * Policy (mirror of backend)
 * --------------------------
 *  - DD/MM only (AU). 05/12 is 5 December, never 12 May.
 *  - Missing year defaults to today.year. No rollover heuristic.
 *  - Two-digit year always 20YY (no sliding window).
 *  - Mixed separators rejected — 22/05-26 returns null.
 *  - Invalid calendar dates return null.
 *  - Future dates are allowed (no client-side block — backend policy).
 */

// Capture groups: year, month, day.
const ISO_RE = /^\s*(\d{4})-(\d{2})-(\d{2})\s*$/;

// Capture groups: 1=day, 2=sep, 3=month, 4=year (optional 2 or 4 digits).
// The back-reference ``\2`` enforces a consistent separator across the
// whole string so 22/05-26 will not match.
const LOOSE_RE = /^\s*(\d{1,2})([/\-.])(\d{1,2})(?:\2(\d{2}|\d{4}))?\s*$/;

function isValidYMD(year: number, month: number, day: number): boolean {
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;
  const d = new Date(year, month - 1, day);
  return (
    d.getFullYear() === year &&
    d.getMonth() === month - 1 &&
    d.getDate() === day
  );
}

/**
 * Parse a loose user-typed date into a ``Date``. Returns ``null`` if
 * the input doesn't match any accepted shape or denotes an invalid
 * calendar date. Use :func:`dateToISO` to canonicalize the result
 * before sending to the API.
 *
 * @param s the candidate date string. Surrounding whitespace is tolerated.
 * @param today reference date for year-defaulting when the input
 *   omits the year. Defaults to ``new Date()`` at call time. Inject
 *   in tests / previews for determinism.
 */
export function parseLooseDate(s: string, today?: Date): Date | null {
  if (typeof s !== 'string') return null;
  const trimmed = s.trim();
  if (trimmed.length === 0) return null;

  const isoMatch = ISO_RE.exec(s);
  if (isoMatch) {
    const year = Number(isoMatch[1]);
    const month = Number(isoMatch[2]);
    const day = Number(isoMatch[3]);
    if (!isValidYMD(year, month, day)) return null;
    return new Date(year, month - 1, day);
  }

  const loose = LOOSE_RE.exec(s);
  if (!loose) return null;

  const day = Number(loose[1]);
  const month = Number(loose[3]);
  const yearStr = loose[4];

  let year: number;
  if (yearStr === undefined) {
    year = (today ?? new Date()).getFullYear();
  } else if (yearStr.length === 2) {
    year = 2000 + Number(yearStr);
  } else {
    year = Number(yearStr);
  }

  if (!isValidYMD(year, month, day)) return null;
  return new Date(year, month - 1, day);
}

/**
 * Convert a ``Date`` into the canonical ISO ``YYYY-MM-DD`` shape the
 * API expects. Uses *local* components (not UTC) so the day boundary
 * matches what the user thinks "today" means on their device.
 */
export function dateToISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/**
 * Today's date as an ISO ``YYYY-MM-DD`` string (local).
 */
export function todayISO(): string {
  return dateToISO(new Date());
}

/**
 * Yesterday's date as an ISO ``YYYY-MM-DD`` string (local).
 */
export function yesterdayISO(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return dateToISO(d);
}

/**
 * Format an ISO ``YYYY-MM-DD`` string into AU display form ``DD/MM/YYYY``.
 * Returns the em-dash placeholder for empty / nullish input, and falls
 * back to the raw input on a non-ISO shape so a bad row never crashes
 * the screen.
 */
export function formatDateAU(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = ISO_RE.exec(iso);
  if (!m) return iso;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

/**
 * First day (local) of the month containing the given ISO date, as
 * ``YYYY-MM-DD``. Local components only, matching :func:`dateToISO`.
 */
export function monthStart(iso: string): string {
  const [y, m] = iso.split('-').map(Number);
  return dateToISO(new Date(y, m - 1, 1));
}

/**
 * Last day (local) of the month containing the given ISO date, as
 * ``YYYY-MM-DD``. ``new Date(y, m, 0)`` is day 0 of the next month =
 * the last day of month ``m`` (handles 28/29/30/31 automatically).
 */
export function monthEnd(iso: string): string {
  const [y, m] = iso.split('-').map(Number);
  return dateToISO(new Date(y, m, 0));
}

/**
 * Shift an ISO date by whole months (local), anchored to the 1st so a
 * long month never overflows (e.g. Jan 31 + 1 month stays in Feb).
 * Returns the first-of-month ISO of the resulting month.
 */
export function shiftMonthISO(iso: string, months: number): string {
  const [y, m] = iso.split('-').map(Number);
  return dateToISO(new Date(y, m - 1 + months, 1));
}

/**
 * Long, localized month label for the month containing ``iso`` — e.g.
 * "June 2026" (en) / "2026年6月" (zh). Uses on-device ``Intl`` (full
 * locale data on iOS); falls back to numeric ``MM/YYYY`` if ``Intl`` is
 * unavailable so a label never crashes the screen.
 */
export function formatMonthLabel(iso: string, locale?: string): string {
  const [y, m] = iso.split('-').map(Number);
  try {
    return new Intl.DateTimeFormat(locale, {
      month: 'long',
      year: 'numeric',
    }).format(new Date(y, m - 1, 1));
  } catch {
    return `${String(m).padStart(2, '0')}/${y}`;
  }
}

/**
 * forey F1: the Today header's date line — the spec's "7月16日 周四"
 * (zh) / "Thu 16 Jul" (en). Composed explicitly: a single Intl call
 * with month:'long' + weekday:'short' yields neither shape, so only
 * the NAMES come from Intl and the order/punctuation is ours.
 */
export function formatTodayLine(iso: string, locale?: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(y, m - 1, d);
  const zh = locale?.startsWith('zh') ?? false;
  try {
    const weekday = new Intl.DateTimeFormat(zh ? 'zh-CN' : 'en-AU', {
      weekday: 'short',
    }).format(date);
    if (zh) return `${m}月${d}日 ${weekday}`;
    // en-AU's CLDR abbreviations are NOT all three letters (June, July,
    // Sept), so en-AU would render "Fri 17 July". en-GB gives the
    // spec's "Jul" — the 3-letter form isn't a locale preference here,
    // it's the design.
    const month = new Intl.DateTimeFormat('en-GB', { month: 'short' }).format(
      date,
    );
    return `${weekday} ${d} ${month}`;
  } catch {
    return formatDateAU(iso);
  }
}
