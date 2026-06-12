/**
 * Shared presentational helpers for the mobile surface.
 *
 * Mobile Polish slice (Half A): extracted from
 * `mobile/src/components/CaptureResultCard.tsx` so the same currency
 * formatting is used everywhere amounts render (capture result card,
 * Recent Captures list, Expense Detail). Behaviour is stricter than
 * the original local helper: any non-numeric input (including the
 * empty string, null, undefined, NaN, or a wire-shape value the
 * backend hasn't sent) renders as the em-dash placeholder rather
 * than leaking the raw value into the UI.
 */

/**
 * Format an amount as ``$1,300.00``.
 *
 * Accepts the wire-shape string (e.g. ``"1300.00"``) or a number.
 * Returns ``"—"`` for any value that does not represent a finite
 * number — null, undefined, empty string, NaN, or a non-numeric
 * string — so a single bad row never crashes the screen and never
 * leaks the raw value verbatim.
 */
export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * Format a labour day count (L-B1/L-B2).
 *
 * Accepts the wire-shape Decimal string (``"0.5"``, ``"3.5"``,
 * ``"12"``) or a number. Whole numbers render bare (``"12"``), halves
 * render with one decimal (``"3.5"``). Non-numeric input renders the
 * em-dash placeholder, mirroring :func:`formatMoney`.
 */
export function formatDays(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return '—';
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}
