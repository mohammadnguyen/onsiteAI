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

const GST_DIVISOR = 1.1;

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * F2 contract GST helpers (display-hint model). ``contract_value_ex_gst``
 * is ALWAYS the canonical ex-GST revenue basis. ``inclusive`` jobs
 * ("Including GST") are entered GROSS and converted on write/display;
 * ``exclusive`` jobs ("No GST (Cash)") are entered as the revenue
 * directly with GST 0. ~1c round-trip drift is accepted (Q3); display
 * is recomputed deterministically so the same value renders each time.
 */

/** Entered (as-displayed) amount -> canonical stored ex-GST value. */
export function contractExGstFromEntered(
  entered: number,
  inclusive: boolean,
): number {
  return inclusive ? round2(entered / GST_DIVISOR) : round2(entered);
}

/** Stored ex-GST value -> the amount to display/edit (gross if inclusive). */
export function contractEnteredFromExGst(
  stored: number,
  inclusive: boolean,
): number {
  return inclusive ? round2(stored * GST_DIVISOR) : round2(stored);
}

/** GST component of an entered amount; reconciles exactly to the stored
 * ex-GST (entered = exGST + gst). 0 for No GST (Cash). */
export function contractGstFromEntered(
  entered: number,
  inclusive: boolean,
): number {
  return inclusive
    ? round2(entered - contractExGstFromEntered(entered, inclusive))
    : 0;
}

/**
 * O2-A: suggested total budget from contract + target margin %.
 *
 * Client-side mirror of the backend's canonical margin math
 * (`backend/app/services/budget_summary.py:_compute_margin_fields`):
 * ``target_cost_limit_ex_gst = contract_ex_gst × (100 − target%) / 100``,
 * quantized to the cent. Margin is profit-on-REVENUE, not markup-on-cost.
 *
 * ``enteredContract`` is the AS-DISPLAYED contract figure (gross when the
 * job is "Including GST"), so we convert to the canonical ex-GST basis
 * first — the suggestion is therefore GST-mode independent, exactly like
 * the stored math. Pure convenience prefill: the caller lets the admin
 * overwrite freely (target cost limit is informational, never enforced).
 */
export function budgetFromContractAndMargin(
  enteredContract: number,
  inclusive: boolean,
  marginPct: number,
): number {
  const exGst = contractExGstFromEntered(enteredContract, inclusive);
  return round2((exGst * (100 - marginPct)) / 100);
}
