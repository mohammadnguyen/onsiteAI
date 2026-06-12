/**
 * Time-of-day helpers for the L-C3 labour tick screen.
 *
 * The labour backend (v15) derives an entry's hours from a same-day
 * start->end TIME range and remains the SINGLE SOURCE OF TRUTH: the
 * client sends canonical ``HH:MM`` strings and the server recomputes the
 * duration and enforces the ordering CHECK. This parser is UX-only — it
 * lets the user type loosely ("7:30", "730", "5pm") and shows a live
 * duration before save; it returns a flag on failure (never throws out
 * of a keystroke handler).
 *
 * Scope mirrors the backend: SAME-DAY only (no date, no timezone), the
 * FULL span with NO break deduction. Ordering (end after start) is
 * checked by the caller. These are NOT payroll concepts — just the hours
 * a worker was on site.
 *
 * Accepted shapes
 * ---------------
 *  - empty            -> no time (valid; both-or-neither handled by caller)
 *  - H / HH           -> hour only      ("7" -> 07:00, "17" -> 17:00)
 *  - H:MM / HH:MM     -> colon form     ("7:30", "07:05"); "." also ok
 *  - HMM / HHMM       -> bare digits    ("730" -> 07:30, "1700" -> 17:00)
 *  - any of the above + am/pm           ("5pm" -> 17:00, "12am" -> 00:00)
 *
 * Policy
 * ------
 *  - With am/pm the hour must be 1..12 (12am = 00:00, 12pm = 12:00).
 *  - Without am/pm the hour is 24-hour (0..23).
 *  - Minutes are 0..59; colon form requires two minute digits.
 *  - Anything else returns ``valid: false``.
 */

export type ParsedTime = {
  /** Canonical ``HH:MM`` to send to the API, or null when the input is empty. */
  value: string | null;
  /** Minutes since local midnight (for duration math), or null when empty. */
  minutes: number | null;
  /** False only when the text is non-empty and unparseable. */
  valid: boolean;
};

const EMPTY: ParsedTime = { value: null, minutes: null, valid: true };
const INVALID: ParsedTime = { value: null, minutes: null, valid: false };

const COLON_RE = /^(\d{1,2})[:.](\d{2})$/;
const MERIDIEM_RE = /\s*([ap])\.?m\.?$/;

/**
 * Parse loosely-typed text into a same-day time. Empty string is a
 * valid "no time"; an unparseable non-empty string is ``valid: false``.
 */
export function parseTimeOfDay(text: string): ParsedTime {
  if (typeof text !== 'string') return INVALID;
  let s = text.trim().toLowerCase();
  if (s.length === 0) return EMPTY;

  // Pull an optional am/pm (or a.m. / p.m.) suffix off the end first.
  let meridiem: 'am' | 'pm' | null = null;
  const mer = MERIDIEM_RE.exec(s);
  if (mer) {
    meridiem = mer[1] === 'a' ? 'am' : 'pm';
    s = s.slice(0, mer.index).trim();
  }
  s = s.replace(/\s+/g, '');
  if (s.length === 0) return INVALID;

  let hour: number;
  let minute: number;
  const colon = COLON_RE.exec(s);
  if (colon) {
    hour = Number(colon[1]);
    minute = Number(colon[2]);
  } else if (/^\d+$/.test(s)) {
    if (s.length <= 2) {
      hour = Number(s);
      minute = 0;
    } else if (s.length === 3) {
      hour = Number(s.slice(0, 1));
      minute = Number(s.slice(1));
    } else if (s.length === 4) {
      hour = Number(s.slice(0, 2));
      minute = Number(s.slice(2));
    } else {
      return INVALID;
    }
  } else {
    return INVALID;
  }

  if (minute < 0 || minute > 59) return INVALID;

  if (meridiem) {
    if (hour < 1 || hour > 12) return INVALID;
    if (meridiem === 'am') hour = hour === 12 ? 0 : hour;
    else hour = hour === 12 ? 12 : hour + 12;
  } else if (hour < 0 || hour > 23) {
    return INVALID;
  }

  const minutes = hour * 60 + minute;
  const value = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  return { value, minutes, valid: true };
}

/**
 * Whole-hours duration between two same-day times, quantised to two
 * decimals to mirror the backend's NUMERIC(4,2) derivation. The caller
 * must have already ensured ``endMinutes > startMinutes``.
 */
export function durationHours(startMinutes: number, endMinutes: number): number {
  return Math.round(((endMinutes - startMinutes) / 60) * 100) / 100;
}

/**
 * Format an hours number for display ("9.5", "8", "9.25"). Non-finite
 * input renders the em-dash placeholder, mirroring formatMoney/formatDays.
 */
export function formatHoursShort(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) {
    return '—';
  }
  return String(Math.round(hours * 100) / 100);
}

/**
 * Reduce a server ``HH:MM:SS`` time to the ``HH:MM`` the inputs display.
 * Returns '' for null/blank so an absent time prefills as an empty box.
 */
export function hhmmFromServer(value: string | null | undefined): string {
  if (!value || typeof value !== 'string') return '';
  const m = /^(\d{2}):(\d{2})/.exec(value);
  return m ? `${m[1]}:${m[2]}` : '';
}

/**
 * Derived state of a start/end pair for one row — the single place the
 * tick screen and the checklist agree on validity, the canonical values
 * to send, and the live duration to show. Mirrors the backend rules:
 * both-or-neither, same-day, end strictly after start.
 */
export type TimeRangeStatus = {
  /** Canonical ``HH:MM`` to send, or null when that side is empty. */
  startValue: string | null;
  endValue: string | null;
  startMinutes: number | null;
  endMinutes: number | null;
  /** A non-empty field could not be parsed. */
  parseError: boolean;
  /** Exactly one side is present (the backend rejects this). */
  onePresent: boolean;
  /** Both present and parsed, but end is not after start. */
  orderError: boolean;
  /** Both present, parsed, end after start — safe to derive + send. */
  ready: boolean;
  /** Derived hours, set only when ``ready``. */
  durationHours: number | null;
};

export function computeTimeRange(
  startText: string,
  endText: string,
): TimeRangeStatus {
  const s = parseTimeOfDay(startText);
  const e = parseTimeOfDay(endText);
  const parseError = !s.valid || !e.valid;
  const startPresent = s.value !== null;
  const endPresent = e.value !== null;
  const onePresent = startPresent !== endPresent;
  let orderError = false;
  let ready = false;
  let dur: number | null = null;
  if (!parseError && startPresent && endPresent) {
    if ((e.minutes as number) <= (s.minutes as number)) {
      orderError = true;
    } else {
      ready = true;
      dur = durationHours(s.minutes as number, e.minutes as number);
    }
  }
  return {
    startValue: s.value,
    endValue: e.value,
    startMinutes: s.minutes,
    endMinutes: e.minutes,
    parseError,
    onePresent,
    orderError,
    ready,
    durationHours: dur,
  };
}
