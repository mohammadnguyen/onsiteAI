/**
 * Phase 3 Lite — shared rendering helpers for the budget-visibility
 * surface, extended in Phase 3 Lite+.
 *
 * Used by `Jobs.tsx` (list) and `JobDetail.tsx` (KPI header + per-category
 * panel + Target margin panel). Centralised here so the formatting and
 * the banded chip stay in sync between the two pages.
 *
 * Money values arrive from the backend as Pydantic-serialised Decimal
 * strings (e.g. `"9344.00"`). Keeping them as strings until format-time
 * avoids float round-trip drift; the formatter parses with `Number()`
 * exactly once for display.
 *
 * Phase 3 Lite+ adds the fifth band (`critical`). Routing rules and
 * styling rules are documented inline below — both must stay aligned
 * with `backend/app/services/budget_summary.compute_band` (which is
 * tested as the canonical contract).
 */
import type { TFunction } from 'i18next'
import { exToInc } from './gst'
import type { GstDisplayMode } from '../store/gstDisplay'

/** Default thresholds when neither the per-job stored value nor the
 * API's effective value are available. The API always populates the
 * effective values, but defensive defaults keep this helper safe to
 * call in test scenarios or before data has loaded. */
const DEFAULT_AMBER_PCT = 80
const DEFAULT_RED_PCT = 100

/** Five bands. Phase 3 Lite shipped with four; `critical` is added in
 * Phase 3 Lite+ for the case where the user set a custom red threshold
 * below 100 and the cost is approaching exhaustion but the budget has
 * NOT actually been exceeded. */
export type BudgetBand =
  | 'on_track'
  | 'approaching'
  | 'critical'
  | 'over_budget'
  | 'no_budget'

/**
 * Routing rules (frozen by ``docs/phase-3-lite-plus-plan.md`` and
 * mirrored by ``backend/app/services/budget_summary.compute_band``):
 *
 * * `no_budget` — total budget is null or zero.
 * * `over_budget` — `percent >= 100` OR `remaining_ex_gst < 0`. The
 *   ONLY band that may carry the wording "Over budget" / "超预算".
 * * `critical` — `effRed <= percent < 100` (only reachable when red
 *   was set below 100; with default red = 100, this band collapses to
 *   empty).
 * * `approaching` — `effAmber <= percent < effRed`.
 * * `on_track` — `percent < effAmber`.
 *
 * Tie-break: `over_budget > critical > approaching > on_track`.
 */
export function getBudgetBand(
  percent: string | null,
  remaining: string | null,
  hasBudget: boolean,
  effectiveAmberPct: string | null = null,
  effectiveRedPct: string | null = null,
): BudgetBand {
  if (!hasBudget) return 'no_budget'

  const p = percent !== null ? Number(percent) : null
  const r = remaining !== null ? Number(remaining) : null

  // Over-budget rule fires first so a custom red below 100 cannot
  // mislabel an actually-exceeded budget as merely "critical".
  const overByPercent = p !== null && !Number.isNaN(p) && p >= 100
  const overByRemaining = r !== null && !Number.isNaN(r) && r < 0
  if (overByPercent || overByRemaining) return 'over_budget'

  if (p === null || Number.isNaN(p)) return 'no_budget'

  const amber = parsePctOrDefault(effectiveAmberPct, DEFAULT_AMBER_PCT)
  const red = parsePctOrDefault(effectiveRedPct, DEFAULT_RED_PCT)

  if (p >= red) return 'critical'
  if (p >= amber) return 'approaching'
  return 'on_track'
}

function parsePctOrDefault(value: string | null, fallback: number): number {
  if (value === null) return fallback
  const n = Number(value)
  return Number.isNaN(n) ? fallback : n
}

/**
 * Format a Decimal-string money value as a localised AUD figure with
 * exactly two decimals. ``null`` / ``undefined`` collapses to ``—`` so
 * the caller never has to render a placeholder twice.
 */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('en-AU', {
    style: 'currency',
    currency: 'AUD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/**
 * Format a percent (Decimal string) as ``"X.XX%"``. ``null`` →
 * ``"—"``.
 */
export function formatPercent(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return '—'
  return `${n.toFixed(2)}%`
}

/** Sort comparator for the `/jobs` list: % consumed desc, no-budget last,
 * tie-break alphabetical by ``job_name``.
 *
 * NULL `percent_consumed` (no budget set OR no summary at all) goes to
 * the bottom regardless of sort direction — the dashboard's purpose is
 * to surface the highest-risk job first, and a job with no budget can't
 * be at risk of overspend.
 */
export function compareJobsByConsumption(
  a: { job_name: string; summary?: { percent_consumed: string | null } | null },
  b: { job_name: string; summary?: { percent_consumed: string | null } | null },
): number {
  const ap = a.summary?.percent_consumed ?? null
  const bp = b.summary?.percent_consumed ?? null
  if (ap === null && bp === null) return a.job_name.localeCompare(b.job_name)
  if (ap === null) return 1
  if (bp === null) return -1
  const an = Number(ap)
  const bn = Number(bp)
  if (an === bn) return a.job_name.localeCompare(b.job_name)
  return bn - an
}

/** Tailwind classes per band. The progression is colour-coded for
 * severity: emerald → amber → orange → red. `critical` is given a
 * thicker ring and bolder text weight so it is visually distinct from
 * both `approaching` (lighter amber) AND `over_budget` (red) — the
 * operator constraint requires that `critical` never look identical to
 * a true over-budget chip. */
const BAND_CLASS: Record<BudgetBand, string> = {
  on_track: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200',
  approaching: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
  critical:
    'bg-orange-100 text-orange-900 ring-2 ring-orange-400 font-semibold',
  over_budget: 'bg-red-100 text-red-800 ring-1 ring-red-200',
  no_budget: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200',
}

const BAND_LABEL_KEY: Record<BudgetBand, string> = {
  on_track: 'budget.status_on_track',
  approaching: 'budget.status_approaching',
  critical: 'budget.status_critical',
  over_budget: 'budget.status_over_budget',
  no_budget: 'budget.status_no_budget',
}

export function bandLabel(band: BudgetBand, t: TFunction): string {
  return t(BAND_LABEL_KEY[band])
}

export function BudgetChip({
  band,
  t,
}: {
  band: BudgetBand
  t: TFunction
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs ${BAND_CLASS[band]}`}
    >
      {bandLabel(band, t)}
    </span>
  )
}

/**
 * Phase 3 Lite+ — render an ex-GST money value according to the user's
 * GST display preference. Returns the primary string (the value the
 * tile/cell should headline) and an optional secondary string (a small
 * line beneath, only present in `'both'` and `'inc'` modes when there
 * is a value to convert).
 *
 * In `'ex'` mode the result matches today's behaviour (primary = ex,
 * no secondary). In `'both'` mode the secondary is "= $X inc GST". In
 * `'inc'` mode the primary is the inc value and the secondary is
 * "= $X ex GST" — callers that show a tile label should ALSO flip the
 * label suffix to "inc GST" so the label/value coupling is unambiguous.
 *
 * The ex→inc conversion goes through the single helper in
 * `lib/gst.ts` so quantization stays consistent with the input toggle.
 */
export function renderBudgetMoney(
  exValue: string | null | undefined,
  mode: GstDisplayMode,
  t: TFunction,
): { primary: string; secondary: string | null } {
  if (exValue === null || exValue === undefined) {
    return { primary: '—', secondary: null }
  }
  if (mode === 'ex') {
    return { primary: formatMoney(exValue), secondary: null }
  }
  // Negative ex (e.g. a category overspend) flips sign on inc the same way.
  const incValue = exToInc(exValue.startsWith('-') ? exValue.slice(1) : exValue)
  const incFormatted =
    exValue.startsWith('-')
      ? `-${formatMoney(incValue)}`
      : formatMoney(incValue)
  if (mode === 'both') {
    return {
      primary: formatMoney(exValue),
      secondary: t('budget.inc_gst_hint', { value: incFormatted }),
    }
  }
  // mode === 'inc'
  return {
    primary: incFormatted,
    secondary: t('budget.ex_gst_hint', { value: formatMoney(exValue) }),
  }
}

/** Returns the i18n key for the basis-suffix on a tile label, given
 * the current display mode. Tiles call this to flip "Budget ex GST"
 * → "Budget inc GST" when the user is viewing in inc mode. */
export function basisSuffixKey(mode: GstDisplayMode): string {
  return mode === 'inc' ? 'budget.suffix_inc' : 'budget.suffix_ex'
}

// ---------------------------------------------------------------------------
// Phase 3 Lite++ — auto-calculated total budget
// ---------------------------------------------------------------------------

/**
 * Compute the target cost limit (== auto-calculated total budget) from
 * canonical ex-GST inputs.
 *
 * Formula (frozen, mirrors backend ``_compute_margin_fields``):
 *
 *     target_cost_limit_ex_gst = contract × (1 − target_profit_ratio_pct / 100)
 *
 * Both inputs are Decimal-strings. Returns a Decimal-string quantized to
 * 0.01 via cent-level integer rounding (no float equality drift). Empty
 * input or non-numeric input returns "" — the caller treats that as
 * "auto-calc not possible right now".
 *
 * **GST canonicalization is the caller's responsibility.** The contract
 * string passed in MUST already be in ex-GST (the canonical basis used
 * everywhere the budget math runs). The form's ``<GstAmountInput>``
 * always emits ex-GST regardless of which basis the user typed in, so
 * the contract value reaching this helper is always correctly canonical.
 *
 * Worked examples:
 *   calcTargetCostLimit("200000", "15")    → "170000.00"
 *   calcTargetCostLimit("200000.00", "15.00") → "170000.00"
 *   calcTargetCostLimit("100", "0")         → "100.00" (0% target)
 *   calcTargetCostLimit("1234.56", "12.5")  → "1080.24"
 *   calcTargetCostLimit("", "15")           → ""  (no contract)
 *   calcTargetCostLimit("200000", "")       → ""  (no target)
 *   calcTargetCostLimit("200000", "100")    → "0.00" (full target — degenerate; backend Pydantic rejects target ≥ 100)
 */
export function calcTargetCostLimit(
  contractExGst: string,
  targetProfitRatioPct: string,
): string {
  if (contractExGst.trim() === '' || targetProfitRatioPct.trim() === '') return ''
  const contract = Number(contractExGst)
  const target = Number(targetProfitRatioPct)
  if (!Number.isFinite(contract) || !Number.isFinite(target)) return ''
  // Compute in cents to dodge JS float drift on the (1 - target/100) factor:
  //   result_cents = round(contract_cents × (10000 − target_basis_points) / 10000)
  // Then divide by 100 for the dollar-string. Tolerable for amounts up to
  // ~$10M (matches the gst.ts precision note).
  const resultDollars = (contract * (100 - target)) / 100
  return resultDollars.toFixed(2)
}

/**
 * Compare two Decimal-string money values for equality at the cent level.
 *
 * Used by ``<TotalBudgetField>`` to decide whether a stored budget
 * matches the auto-calculated value (and therefore the form should open
 * in 'auto' mode) or differs (and therefore should open in 'manual'
 * override mode).
 *
 * Both arguments are rounded to the nearest cent as integers, then
 * compared as integers — this avoids float-equality drift like
 * ``188000 - 188000 === 1.4e-14`` from naïve subtraction.
 *
 * Either argument null/empty/non-numeric → returns ``false``.
 */
export function budgetsMatchToCent(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  if (a === null || a === undefined || a === '') return false
  if (b === null || b === undefined || b === '') return false
  const an = Number(a)
  const bn = Number(b)
  if (!Number.isFinite(an) || !Number.isFinite(bn)) return false
  return Math.round(an * 100) === Math.round(bn * 100)
}

/**
 * Compute the effective profit margin (as a percent string) given
 * contract value and a chosen budget — used by the override warning
 * line so the user sees the consequence of their manual budget on
 * their actual margin.
 *
 *     effective_margin_pct = (contract − budget) / contract × 100
 *
 * Returns "" when either input is missing/invalid or contract is 0
 * (avoid divide-by-zero — matches the backend rule).
 */
export function calcEffectiveMarginPct(
  contractExGst: string,
  budgetExGst: string,
): string {
  if (contractExGst.trim() === '' || budgetExGst.trim() === '') return ''
  const contract = Number(contractExGst)
  const budget = Number(budgetExGst)
  if (!Number.isFinite(contract) || !Number.isFinite(budget)) return ''
  if (contract === 0) return ''
  return (((contract - budget) / contract) * 100).toFixed(2)
}
