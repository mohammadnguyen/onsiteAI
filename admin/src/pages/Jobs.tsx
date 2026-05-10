import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useCreateJob, useJobs } from '../api/hooks/useJobs'
import { AppShell } from '../components/AppShell'
import { GstAmountInput } from '../components/GstAmountInput'
import { Modal } from '../components/Modal'
import { TotalBudgetField } from '../components/TotalBudgetField'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'
import {
  BudgetChip,
  compareJobsByConsumption,
  formatMoney,
  formatPercent,
  getBudgetBand,
  renderBudgetMoney,
} from '../lib/budget'
import { useGstDisplay } from '../store/gstDisplay'

type JobStatus = components['schemas']['JobStatus']

export function Jobs() {
  const { t } = useTranslation()
  const jobs = useJobs()
  const createJob = useCreateJob()

  const [open, setOpen] = useState(false)
  const [jobName, setJobName] = useState('')
  const [jobCode, setJobCode] = useState('')
  const [siteAddress, setSiteAddress] = useState('')
  const [contractValue, setContractValue] = useState('')
  // Phase 3 Lite++: target profit % is now part of the New Job modal so
  // <TotalBudgetField> can derive the budget from contract × (1 − target/100).
  const [targetProfit, setTargetProfit] = useState('')
  const [totalBudget, setTotalBudget] = useState('')
  const [status, setStatus] = useState<JobStatus>('active')
  const [formError, setFormError] = useState<string | null>(null)

  const resetForm = () => {
    setJobName('')
    setJobCode('')
    setSiteAddress('')
    setContractValue('')
    setTargetProfit('')
    setTotalBudget('')
    setStatus('active')
    setFormError(null)
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    try {
      await createJob.mutateAsync({
        job_name: jobName,
        job_code: jobCode.trim() ? jobCode : null,
        site_address: siteAddress.trim() ? siteAddress : null,
        contract_value_ex_gst: contractValue ? contractValue : null,
        // <TotalBudgetField> already kept totalBudget in sync with the
        // auto-calc when in auto mode, so submitting `totalBudget` is
        // correct for both modes. Explicit "" → null for empty.
        total_budget_ex_gst: totalBudget ? totalBudget : null,
        target_profit_ratio_pct: targetProfit ? targetProfit : null,
        status,
      })
      resetForm()
      setOpen(false)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">{t('jobs.title')}</h1>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800"
        >
          {t('jobs.new')}
        </button>
      </div>

      {jobs.isLoading && <p className="text-sm text-slate-600">{t('common.loading')}</p>}
      {jobs.isError && (
        <p className="text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(jobs.error)}
        </p>
      )}
      {jobs.data && jobs.data.length === 0 && (
        <p className="text-sm text-slate-600">{t('jobs.none')}</p>
      )}
      {jobs.data && jobs.data.length > 0 && <JobsTable jobs={jobs.data} />}

      <Modal open={open} onClose={() => setOpen(false)} title={t('jobs.new')}>
        <form onSubmit={onSubmit} className="space-y-3">
          <Field label={t('jobs.job_name')} required>
            <input
              required
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('jobs.job_code')}>
            <input
              value={jobCode}
              onChange={(e) => setJobCode(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('jobs.site_address')}>
            <input
              value={siteAddress}
              onChange={(e) => setSiteAddress(e.target.value)}
              className={inputClass}
            />
          </Field>
          <GstAmountInput
            label={t('jobs.contract_value_input')}
            value={contractValue}
            onChange={setContractValue}
          />
          <Field label={t('jobs.target_profit_ratio_pct')}>
            <input
              type="number"
              step="0.01"
              min="0"
              max="99.99"
              value={targetProfit}
              onChange={(e) => setTargetProfit(e.target.value)}
              className={inputClass}
            />
          </Field>
          <TotalBudgetField
            contractValueExGst={contractValue}
            targetProfitRatioPct={targetProfit}
            value={totalBudget}
            onChange={setTotalBudget}
          />
          <Field label={t('jobs.status')}>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as JobStatus)}
              className={inputClass}
            >
              <option value="active">{t('jobs.status_active')}</option>
              <option value="completed">{t('jobs.status_completed')}</option>
            </select>
          </Field>
          {formError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
              {formError}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setOpen(false)} className={btnSecondary}>
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={createJob.isPending} className={btnPrimary}>
              {createJob.isPending ? t('common.loading') : t('common.save')}
            </button>
          </div>
        </form>
      </Modal>
    </AppShell>
  )
}

type JobRow = components['schemas']['JobPublic']

/**
 * Phase 3 Lite jobs table.
 *
 * Default sort is `% consumed` desc so the highest-risk job lands on
 * top, with NULL-budget rows pushed to the bottom (alphabetical
 * tie-break). Sort lives client-side per the plan — this page handles
 * 5–20 rows in real use; full Phase 3 can move sorting server-side.
 *
 * The `Spent inc GST` / `Spent ex GST` columns sit beside `Budget ex
 * GST` / `Remaining ex GST` so the user can read GST-basis at a glance
 * (the labels never collapse to bare "Spent" / "Budget"; that
 * convention is frozen by docs/phase-3-lite-plan.md).
 */
function JobsTable({ jobs }: { jobs: JobRow[] }) {
  const { t } = useTranslation()
  const { mode: gstMode } = useGstDisplay()
  const sorted = useMemo(() => [...jobs].sort(compareJobsByConsumption), [jobs])

  // Headers respect the GST display preference: "Budget ex GST" / "Budget
  // inc GST" / "Remaining ex GST" / "Remaining inc GST". The basis suffix
  // is the same for both columns so the user's eye reads the basis once.
  const budgetHeader =
    gstMode === 'inc' ? t('budget.budget_inc_gst') : t('budget.budget_ex_gst')
  const remainingHeader =
    gstMode === 'inc' ? t('budget.remaining_inc_gst') : t('budget.remaining_ex_gst')

  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            <th className="text-left px-4 py-2 font-medium">{t('jobs.job_name')}</th>
            <th className="text-left px-4 py-2 font-medium">{t('jobs.job_code')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.spent_inc_gst')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.spent_ex_gst')}</th>
            <th className="text-right px-4 py-2 font-medium">{budgetHeader}</th>
            <th className="text-right px-4 py-2 font-medium">{remainingHeader}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.percent_consumed')}</th>
            <th className="text-left px-4 py-2 font-medium">{t('jobs.status')}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((job) => {
            const s = job.summary
            const hasBudget =
              s != null &&
              s.total_budget_ex_gst !== null &&
              Number(s.total_budget_ex_gst) > 0
            // Phase 3 Lite+ — chip uses per-job effective thresholds plus
            // the remaining_ex_gst safety check so a budget exhaustion at
            // <100% still surfaces as `over_budget`.
            const band = getBudgetBand(
              s?.percent_consumed ?? null,
              s?.remaining_ex_gst ?? null,
              hasBudget,
              s?.effective_warning_amber_pct ?? null,
              s?.effective_warning_red_pct ?? null,
            )
            const budgetCell = renderBudgetMoney(
              s?.total_budget_ex_gst ?? null,
              gstMode,
              t,
            )
            const remainingCell = renderBudgetMoney(
              s?.remaining_ex_gst ?? null,
              gstMode,
              t,
            )
            return (
              <tr key={job.job_id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/jobs/${job.job_id}`} className="text-slate-900 hover:underline">
                    {job.job_name}
                  </Link>
                </td>
                <td className="px-4 py-2 text-slate-700">{job.job_code ?? '—'}</td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  {formatMoney(s?.actual_inc_gst)}
                </td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  {formatMoney(s?.actual_ex_gst)}
                </td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  <div>{budgetCell.primary}</div>
                  {budgetCell.secondary && (
                    <div className="text-xs text-slate-500">{budgetCell.secondary}</div>
                  )}
                </td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  <div>{remainingCell.primary}</div>
                  {remainingCell.secondary && (
                    <div className="text-xs text-slate-500">{remainingCell.secondary}</div>
                  )}
                </td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  <div className="flex items-center justify-end gap-2">
                    <span>{formatPercent(s?.percent_consumed ?? null)}</span>
                    <BudgetChip band={band} t={t} />
                  </div>
                </td>
                <td className="px-4 py-2 text-slate-700">
                  {job.status === 'active'
                    ? t('jobs.status_active')
                    : t('jobs.status_completed')}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
const btnPrimary =
  'bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50'
const btnSecondary =
  'bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200'

function Field({
  label,
  children,
  required,
}: {
  label: string
  children: React.ReactNode
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </span>
      {children}
    </label>
  )
}
