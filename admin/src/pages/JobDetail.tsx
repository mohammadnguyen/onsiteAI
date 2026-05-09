import { useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import {
  useAddAlias,
  useAddCategoryBudget,
  useCategories,
  useJob,
} from '../api/hooks/useJobs'
import { useJobBudgetSummary } from '../api/hooks/useBudgetSummary'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'
import {
  BudgetChip,
  formatMoney,
  formatPercent,
  getBudgetBand,
} from '../lib/budget'

type LanguageCode = components['schemas']['LanguageCode']
type JobBudgetSummary = components['schemas']['JobBudgetSummary']
type CategoryBudgetRow = components['schemas']['CategoryBudgetRow']

export function JobDetail() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const job = useJob(id)
  const summary = useJobBudgetSummary(id)
  const categories = useCategories()
  const addAlias = useAddAlias(id)
  const addBudget = useAddCategoryBudget(id)

  const [aliasText, setAliasText] = useState('')
  const [aliasLang, setAliasLang] = useState<LanguageCode | ''>('')
  const [aliasError, setAliasError] = useState<string | null>(null)

  const [categoryId, setCategoryId] = useState('')
  const [amount, setAmount] = useState('')
  const [budgetError, setBudgetError] = useState<string | null>(null)

  const submitAlias = async (e: FormEvent) => {
    e.preventDefault()
    setAliasError(null)
    try {
      await addAlias.mutateAsync({
        alias_text: aliasText,
        language_code: aliasLang === '' ? null : aliasLang,
      })
      setAliasText('')
      setAliasLang('')
    } catch (err) {
      setAliasError(extractErrorMessage(err))
    }
  }

  const submitBudget = async (e: FormEvent) => {
    e.preventDefault()
    setBudgetError(null)
    try {
      await addBudget.mutateAsync({
        category_id: categoryId,
        budget_amount_ex_gst: amount,
      })
      setCategoryId('')
      setAmount('')
    } catch (err) {
      setBudgetError(extractErrorMessage(err))
    }
  }

  return (
    <AppShell>
      <Link to="/jobs" className="text-sm text-slate-600 hover:underline">
        &larr; {t('job.back')}
      </Link>

      {job.isLoading && <p className="mt-4 text-sm text-slate-600">{t('common.loading')}</p>}
      {job.isError && (
        <p className="mt-4 text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(job.error)}
        </p>
      )}

      {job.data && (
        <div className="mt-4 space-y-6">
          <div className="bg-white rounded-lg border border-slate-200 p-6">
            <h1 className="text-2xl font-semibold text-slate-900 mb-4">{job.data.job_name}</h1>
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
              <Info label={t('jobs.job_code')} value={job.data.job_code ?? '—'} />
              <Info
                label={t('jobs.status')}
                value={
                  job.data.status === 'active'
                    ? t('jobs.status_active')
                    : t('jobs.status_completed')
                }
              />
              <Info label={t('jobs.site_address')} value={job.data.site_address ?? '—'} />
              <Info
                label={t('jobs.contract_value')}
                value={job.data.contract_value_ex_gst ?? '—'}
              />
              <Info
                label={t('jobs.total_budget')}
                value={job.data.total_budget_ex_gst ?? '—'}
              />
            </dl>
          </div>

          {summary.data && <KpiHeader summary={summary.data} t={t} />}
          {summary.data && <BudgetVsActual summary={summary.data} t={t} />}

          <section className="bg-white rounded-lg border border-slate-200 p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-4">{t('job.aliases')}</h2>
            {job.data.aliases.length === 0 ? (
              <p className="text-sm text-slate-600 mb-4">{t('job.no_aliases')}</p>
            ) : (
              <ul className="mb-4 space-y-1">
                {job.data.aliases.map((alias) => (
                  <li key={alias.alias_id} className="text-sm text-slate-800">
                    {alias.alias_text}
                    {alias.language_code && (
                      <span className="ml-2 text-xs text-slate-500">
                        ({alias.language_code})
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <form onSubmit={submitAlias} className="flex flex-wrap gap-2 items-end">
              <label className="flex-1 min-w-[180px]">
                <span className="block text-xs font-medium text-slate-600 mb-1">
                  {t('job.alias_text')}
                </span>
                <input
                  required
                  value={aliasText}
                  onChange={(e) => setAliasText(e.target.value)}
                  className={inputClass}
                />
              </label>
              <label>
                <span className="block text-xs font-medium text-slate-600 mb-1">
                  {t('job.language')}
                </span>
                <select
                  value={aliasLang}
                  onChange={(e) => setAliasLang(e.target.value as LanguageCode | '')}
                  className={inputClass}
                >
                  <option value="">—</option>
                  <option value="en">EN</option>
                  <option value="zh">ZH</option>
                </select>
              </label>
              <button type="submit" disabled={addAlias.isPending} className={btnPrimary}>
                {addAlias.isPending ? t('common.loading') : t('job.add_alias')}
              </button>
            </form>
            {aliasError && (
              <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                {aliasError}
              </div>
            )}
          </section>

          <section className="bg-white rounded-lg border border-slate-200 p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-4">{t('job.budgets')}</h2>
            {job.data.category_budgets.length === 0 ? (
              <p className="text-sm text-slate-600 mb-4">{t('job.no_budgets')}</p>
            ) : (
              <table className="w-full text-sm mb-4">
                <thead className="text-slate-600">
                  <tr>
                    <th className="text-left py-1 font-medium">{t('job.category')}</th>
                    <th className="text-left py-1 font-medium">{t('job.amount')}</th>
                  </tr>
                </thead>
                <tbody>
                  {job.data.category_budgets.map((b) => (
                    <tr key={b.budget_id} className="border-t border-slate-100">
                      <td className="py-1 text-slate-800">{b.category.category_name}</td>
                      <td className="py-1 text-slate-800">{b.budget_amount_ex_gst}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <form onSubmit={submitBudget} className="flex flex-wrap gap-2 items-end">
              <label className="flex-1 min-w-[200px]">
                <span className="block text-xs font-medium text-slate-600 mb-1">
                  {t('job.category')}
                </span>
                <select
                  required
                  value={categoryId}
                  onChange={(e) => setCategoryId(e.target.value)}
                  className={inputClass}
                >
                  <option value="" disabled>
                    —
                  </option>
                  {categories.data?.map((c) => (
                    <option key={c.category_id} value={c.category_id}>
                      {c.category_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="block text-xs font-medium text-slate-600 mb-1">
                  {t('job.amount')}
                </span>
                <input
                  required
                  type="number"
                  step="0.01"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className={inputClass}
                />
              </label>
              <button type="submit" disabled={addBudget.isPending} className={btnPrimary}>
                {addBudget.isPending ? t('common.loading') : t('job.add_budget')}
              </button>
            </form>
            {budgetError && (
              <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                {budgetError}
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500 uppercase">{label}</dt>
      <dd className="text-slate-900">{value}</dd>
    </div>
  )
}

/**
 * Phase 3 Lite — KPI header row.
 *
 * Four primary tiles labelled exactly per the plan: `Spent inc GST`,
 * `Spent ex GST`, `Budget ex GST`, `Remaining ex GST`. The `% consumed`
 * pill sits to the right (or below on narrow widths). The Spent inc GST
 * tile carries a secondary line with the GST split when non-zero so the
 * user can sanity-check the inclusive total.
 */
function KpiHeader({
  summary,
  t,
}: {
  summary: JobBudgetSummary
  t: TFunction
}) {
  const hasBudget =
    summary.total_budget_ex_gst !== null && Number(summary.total_budget_ex_gst) > 0
  const band = getBudgetBand(summary.percent_consumed, hasBudget)
  const gst = Number(summary.gst_amount)

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <KpiTile
          label={t('budget.spent_inc_gst')}
          value={formatMoney(summary.actual_inc_gst)}
          secondary={
            gst > 0
              ? `${t('budget.plus_gst')} ${formatMoney(summary.gst_amount)}`
              : undefined
          }
        />
        <KpiTile
          label={t('budget.spent_ex_gst')}
          value={formatMoney(summary.actual_ex_gst)}
        />
        <KpiTile
          label={t('budget.budget_ex_gst')}
          value={formatMoney(summary.total_budget_ex_gst)}
          secondary={hasBudget ? undefined : t('budget.no_budget_set')}
        />
        <KpiTile
          label={t('budget.remaining_ex_gst')}
          value={formatMoney(summary.remaining_ex_gst)}
          chip={!hasBudget ? <BudgetChip band="no_budget" t={t} /> : undefined}
        />
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4 flex flex-col items-start justify-center">
          <div className="text-xs font-medium text-slate-500 uppercase">
            {t('budget.percent_consumed')}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-xl font-semibold text-slate-900 tabular-nums">
              {formatPercent(summary.percent_consumed)}
            </span>
            <BudgetChip band={band} t={t} />
          </div>
        </div>
      </div>
    </section>
  )
}

function KpiTile({
  label,
  value,
  secondary,
  chip,
}: {
  label: string
  value: string
  secondary?: string
  chip?: React.ReactNode
}) {
  return (
    <div className="rounded-md border border-slate-200 p-4">
      <div className="text-xs font-medium text-slate-500 uppercase">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-900 tabular-nums">{value}</div>
      {secondary && (
        <div className="mt-1 text-xs text-slate-600 tabular-nums">{secondary}</div>
      )}
      {chip && <div className="mt-2">{chip}</div>}
    </div>
  )
}

/**
 * Phase 3 Lite — per-category Actual-vs-Budget panel.
 *
 * One row per category that has either a budget row or at least one
 * non-rejected expense on the job. Sort: % consumed desc among rows
 * with budgets; budget-less rows go last (alphabetical by name).
 *
 * Empty-state hints sit above the table when no expenses or no budgets
 * exist; the table still renders the rows so the user can see the
 * actual values even when there's nothing to compare against.
 */
function BudgetVsActual({
  summary,
  t,
}: {
  summary: JobBudgetSummary
  t: TFunction
}) {
  const sorted = useMemo(() => sortCategoryRows(summary.categories), [summary.categories])
  const hasAnyRow = sorted.length > 0
  const allBudgetless = sorted.every((r) => r.budget_ex_gst === null)

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-6">
      <h2 className="text-lg font-semibold text-slate-900 mb-4">
        {t('budget.section_title')}
      </h2>
      {!hasAnyRow && (
        <p className="text-sm text-slate-600 mb-2">{t('budget.empty_no_expenses')}</p>
      )}
      {hasAnyRow && allBudgetless && (
        <p className="text-sm text-slate-600 mb-2">{t('budget.empty_no_budgets')}</p>
      )}
      {hasAnyRow && (
        <table className="w-full text-sm">
          <thead className="text-slate-600">
            <tr>
              <th className="text-left py-1 font-medium">{t('budget.col_category')}</th>
              <th className="text-right py-1 font-medium">{t('budget.actual_ex_gst')}</th>
              <th className="text-right py-1 font-medium">{t('budget.budget_ex_gst')}</th>
              <th className="text-right py-1 font-medium">{t('budget.remaining_ex_gst')}</th>
              <th className="text-left py-1 font-medium pl-4">{t('budget.col_status')}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const hasBudget =
                row.budget_ex_gst !== null && Number(row.budget_ex_gst) > 0
              // Per-category percent for chip purposes; we don't render the
              // number here (the panel is busy enough), but the chip uses
              // the same banding scheme as the job-level chip.
              const percent =
                hasBudget && row.budget_ex_gst !== null
                  ? (
                      (Number(row.actual_ex_gst) / Number(row.budget_ex_gst)) *
                      100
                    ).toFixed(2)
                  : null
              const band = getBudgetBand(percent, hasBudget)
              return (
                <tr key={row.category_id} className="border-t border-slate-100">
                  <td className="py-1 text-slate-800">{row.category_name}</td>
                  <td className="py-1 text-slate-800 text-right tabular-nums">
                    {formatMoney(row.actual_ex_gst)}
                  </td>
                  <td className="py-1 text-slate-800 text-right tabular-nums">
                    {formatMoney(row.budget_ex_gst)}
                  </td>
                  <td className="py-1 text-slate-800 text-right tabular-nums">
                    {formatMoney(row.remaining_ex_gst)}
                  </td>
                  <td className="py-1 pl-4">
                    <BudgetChip band={band} t={t} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}

function sortCategoryRows(rows: CategoryBudgetRow[]): CategoryBudgetRow[] {
  return [...rows].sort((a, b) => {
    const ah =
      a.budget_ex_gst !== null && Number(a.budget_ex_gst) > 0
    const bh =
      b.budget_ex_gst !== null && Number(b.budget_ex_gst) > 0
    if (ah && !bh) return -1
    if (!ah && bh) return 1
    if (ah && bh) {
      // Both have budgets — % consumed desc (highest spend ratio first).
      const ap = Number(a.actual_ex_gst) / Number(a.budget_ex_gst)
      const bp = Number(b.actual_ex_gst) / Number(b.budget_ex_gst)
      if (ap !== bp) return bp - ap
    }
    return a.category_name.localeCompare(b.category_name)
  })
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
const btnPrimary =
  'bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50'
