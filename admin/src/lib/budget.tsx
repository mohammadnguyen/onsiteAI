/**
 * Phase 3 Lite — shared rendering helpers for the budget-visibility surface.
 *
 * Used by `Jobs.tsx` (list) and `JobDetail.tsx` (KPI header + per-category
 * panel). Centralised here so the formatting and the banded chip stay in
 * sync between the two pages.
 *
 * Money values arrive from the backend as Pydantic-serialised Decimal
 * strings (e.g. `"9344.00"`). Keeping them as strings until format-time
 * avoids float round-trip drift; the formatter parses with `Number()`
 * exactly once for display.
 */
import type { TFunction } from 'i18next'

/** % thresholds frozen by docs/phase-3-lite-plan.md. */
const APPROACHING_AT = 80
const OVER_AT = 100

export type BudgetBand =
  | 'on_track'
  | 'approaching'
  | 'over_budget'
  | 'no_budget'

/**
 * Map a percent-consumed string + budget presence onto the four banded
 * states. ``percent`` is null when the backend can't compute it (no
 * budget set, NULL or zero).
 */
export function getBudgetBand(
  percent: string | null,
  hasBudget: boolean,
): BudgetBand {
  if (!hasBudget || percent === null) return 'no_budget'
  const p = Number(percent)
  if (Number.isNaN(p)) return 'no_budget'
  if (p >= OVER_AT) return 'over_budget'
  if (p >= APPROACHING_AT) return 'approaching'
  return 'on_track'
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

/** Tailwind classes per band — kept beside `getBudgetBand` so the four
 * states stay visually consistent across the list page and the detail
 * page. */
const BAND_CLASS: Record<BudgetBand, string> = {
  on_track: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200',
  approaching: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
  over_budget: 'bg-red-100 text-red-800 ring-1 ring-red-200',
  no_budget: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200',
}

const BAND_LABEL_KEY: Record<BudgetBand, string> = {
  on_track: 'budget.status_on_track',
  approaching: 'budget.status_approaching',
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
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${BAND_CLASS[band]}`}
    >
      {bandLabel(band, t)}
    </span>
  )
}
