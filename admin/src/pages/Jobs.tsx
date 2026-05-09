import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useCreateJob, useJobs } from '../api/hooks/useJobs'
import { AppShell } from '../components/AppShell'
import { Modal } from '../components/Modal'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'
import {
  BudgetChip,
  compareJobsByConsumption,
  formatMoney,
  formatPercent,
  getBudgetBand,
} from '../lib/budget'

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
  const [totalBudget, setTotalBudget] = useState('')
  const [status, setStatus] = useState<JobStatus>('active')
  const [formError, setFormError] = useState<string | null>(null)

  const resetForm = () => {
    setJobName('')
    setJobCode('')
    setSiteAddress('')
    setContractValue('')
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
        total_budget_ex_gst: totalBudget ? totalBudget : null,
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
          <Field label={t('jobs.contract_value')}>
            <input
              type="number"
              step="0.01"
              value={contractValue}
              onChange={(e) => setContractValue(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('jobs.total_budget')}>
            <input
              type="number"
              step="0.01"
              value={totalBudget}
              onChange={(e) => setTotalBudget(e.target.value)}
              className={inputClass}
            />
          </Field>
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
  const sorted = useMemo(() => [...jobs].sort(compareJobsByConsumption), [jobs])

  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            <th className="text-left px-4 py-2 font-medium">{t('jobs.job_name')}</th>
            <th className="text-left px-4 py-2 font-medium">{t('jobs.job_code')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.spent_inc_gst')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.spent_ex_gst')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.budget_ex_gst')}</th>
            <th className="text-right px-4 py-2 font-medium">{t('budget.remaining_ex_gst')}</th>
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
            const band = getBudgetBand(s?.percent_consumed ?? null, hasBudget)
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
                  {formatMoney(s?.total_budget_ex_gst ?? null)}
                </td>
                <td className="px-4 py-2 text-slate-800 text-right tabular-nums">
                  {formatMoney(s?.remaining_ex_gst ?? null)}
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
