import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useExpenses, type ExpenseFilters } from '../api/hooks/useExpenses'
import { useExpensesExcel } from '../api/hooks/useExpensesExcel'
import { useJobs, useCategories } from '../api/hooks/useJobs'
import { useSuppliers } from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type ReviewStatus = components['schemas']['ReviewStatus']
type ReceiptStatus = components['schemas']['ReceiptStatus']

export function Expenses() {
  const { t } = useTranslation()
  const [jobId, setJobId] = useState<string>('')
  const [status, setStatus] = useState<ReviewStatus | ''>('')
  const [receiptStatus, setReceiptStatus] = useState<ReceiptStatus | ''>('')
  const [from, setFrom] = useState<string>('')
  const [to, setTo] = useState<string>('')

  // Phase 4 — Excel export filters are intentionally SEPARATE from the
  // page's table filters: the page filters control what's visible in
  // the table; the export filters control what's written to the
  // workbook. The export uses its own inclusion rule (reviewed by
  // default; pending only when include_pending=true; rejected always
  // excluded) so a single shared status filter would be a category
  // mismatch.
  const [exportFrom, setExportFrom] = useState<string>('')
  const [exportTo, setExportTo] = useState<string>('')
  const [includePending, setIncludePending] = useState<boolean>(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [lastDownloaded, setLastDownloaded] = useState<string | null>(null)

  const filters: ExpenseFilters = useMemo(
    () => ({
      job_id: jobId || null,
      status: (status || null) as ReviewStatus | null,
      receipt_status: (receiptStatus || null) as ReceiptStatus | null,
      from: from || null,
      to: to || null,
    }),
    [jobId, status, receiptStatus, from, to],
  )

  const expenses = useExpenses(filters)
  const jobs = useJobs()
  const categories = useCategories()
  const suppliers = useSuppliers()
  const download = useExpensesExcel()

  // Preview count for the export panel: query the export's filter set
  // without a status filter (the export decides status via its own
  // inclusion rule, not the page's). Counted client-side after
  // applying the inclusion rule. Best-effort accuracy for V1 row
  // counts; pagination beyond the limit would under-count and the
  // empty-state warning would over-fire — both acceptable failure
  // modes for an advisory preview.
  // Backend caps GET /expenses ``limit`` at 500. The preview pulls one
  // page at that cap and counts client-side; for V1 row counts this is
  // more than sufficient. If a future trial pushes past 500 expenses in
  // a date window the count under-reports — that's a known minor risk
  // (warning over-fires, not under-fires, on small overruns) flagged
  // below for Batch 2 known risks.
  const previewFilters: ExpenseFilters = useMemo(
    () => ({
      from: exportFrom || null,
      to: exportTo || null,
      limit: 500,
    }),
    [exportFrom, exportTo],
  )
  const previewQuery = useExpenses(previewFilters)
  const previewCount = useMemo(() => {
    const items = previewQuery.data?.items ?? []
    return items.filter((e) => {
      if (e.review_status === 'rejected') return false
      if (e.review_status === 'pending' && !includePending) return false
      return true
    }).length
  }, [previewQuery.data, includePending])

  const previewIsZero =
    !previewQuery.isLoading && previewQuery.data !== undefined && previewCount === 0

  const handleDownload = async () => {
    setExportError(null)
    setLastDownloaded(null)
    try {
      const filename = await download.mutateAsync({
        from_date: exportFrom || undefined,
        to_date: exportTo || undefined,
        include_pending: includePending,
      })
      setLastDownloaded(filename)
    } catch (err) {
      setExportError(extractErrorMessage(err))
    }
  }

  const jobMap = useMemo(() => {
    const m = new Map<string, string>()
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name))
    return m
  }, [jobs.data])

  const categoryMap = useMemo(() => {
    const m = new Map<string, string>()
    categories.data?.forEach((c) => m.set(c.category_id, c.category_name))
    return m
  }, [categories.data])

  const supplierMap = useMemo(() => {
    const m = new Map<string, string>()
    suppliers.data?.forEach((s) => m.set(s.supplier_id, s.supplier_name))
    return m
  }, [suppliers.data])

  const clearFilters = () => {
    setJobId('')
    setStatus('')
    setReceiptStatus('')
    setFrom('')
    setTo('')
  }

  const renderStatus = (s: ReviewStatus) => {
    const label =
      s === 'pending'
        ? t('expenses.status_pending')
        : s === 'reviewed'
          ? t('expenses.status_reviewed')
          : t('expenses.status_rejected')
    const color =
      s === 'pending'
        ? 'bg-amber-100 text-amber-800'
        : s === 'reviewed'
          ? 'bg-emerald-100 text-emerald-800'
          : 'bg-red-100 text-red-800'
    return (
      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>
        {label}
      </span>
    )
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">{t('expenses.title')}</h1>
      </div>

      {/* Phase 4 — Excel export panel. Sits above the table filter bar so
         the export is a deliberate, separate action; the user understands
         that the workbook is generated server-side from its own inclusion
         rule, not from the current table view. */}
      <div className="bg-white rounded-lg border border-slate-200 p-4 mb-4">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">
          {t('expenses.export_title')}
        </h2>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-xs font-medium text-slate-600">
            <span className="block mb-1">{t('expenses.export_from')}</span>
            <input
              type="date"
              value={exportFrom}
              onChange={(e) => setExportFrom(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="text-xs font-medium text-slate-600">
            <span className="block mb-1">{t('expenses.export_to')}</span>
            <input
              type="date"
              value={exportTo}
              onChange={(e) => setExportTo(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="text-xs font-medium text-slate-700 flex items-center gap-1.5 pb-2">
            <input
              type="checkbox"
              checked={includePending}
              onChange={(e) => setIncludePending(e.target.checked)}
              className="accent-slate-900"
            />
            <span>{t('expenses.export_include_pending')}</span>
          </label>
          <button
            type="button"
            onClick={handleDownload}
            disabled={download.isPending}
            className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
          >
            {download.isPending
              ? t('common.loading')
              : t('expenses.export_download')}
          </button>
        </div>
        <p className="text-xs text-slate-500 mt-2">
          {t('expenses.export_inclusion_hint')}
        </p>
        {/* Empty-state warning — fires when the preview query confirms
           zero matching expenses for the current filter + inclusion
           rule. Distinct visual style (amber) so the user notices
           before clicking Download. */}
        {previewIsZero && (
          <p className="text-xs text-amber-700 mt-1 font-medium">
            {t('expenses.export_empty_warning')}
          </p>
        )}
        {/* Post-download success indicator — confirms the filename so
           the user can locate the saved file, especially important when
           CJK job names route through the RFC 5987 filename* form. */}
        {lastDownloaded && (
          <p className="text-xs text-emerald-700 mt-2">
            {t('expenses.export_downloaded', { filename: lastDownloaded })}
          </p>
        )}
        {exportError && (
          <p className="text-xs text-red-600 mt-2">
            {t('common.error')}: {exportError}
          </p>
        )}
      </div>

      <div className="bg-white rounded-lg border border-slate-200 p-4 mb-4 flex flex-wrap gap-3 items-end">
        <label className="text-xs font-medium text-slate-600">
          <span className="block mb-1">{t('expenses.filter_status')}</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as ReviewStatus | '')}
            className={inputClass}
          >
            <option value="">{t('expenses.filter_all')}</option>
            <option value="pending">{t('expenses.status_pending')}</option>
            <option value="reviewed">{t('expenses.status_reviewed')}</option>
            <option value="rejected">{t('expenses.status_rejected')}</option>
          </select>
        </label>
        <label className="text-xs font-medium text-slate-600 min-w-[180px]">
          <span className="block mb-1">{t('expenses.filter_job')}</span>
          <select
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
            className={inputClass}
          >
            <option value="">{t('expenses.filter_all')}</option>
            {jobs.data?.map((j) => (
              <option key={j.job_id} value={j.job_id}>
                {j.job_name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs font-medium text-slate-600">
          <span className="block mb-1">{t('expenses.filter_receipt')}</span>
          <select
            value={receiptStatus}
            onChange={(e) => setReceiptStatus(e.target.value as ReceiptStatus | '')}
            className={inputClass}
          >
            <option value="">{t('expenses.filter_all')}</option>
            <option value="no_receipt">{t('expenses.receipt_no_receipt')}</option>
            <option value="expected_later">{t('expenses.receipt_expected_later')}</option>
          </select>
        </label>
        <label className="text-xs font-medium text-slate-600">
          <span className="block mb-1">{t('expenses.filter_from')}</span>
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className={inputClass}
          />
        </label>
        <label className="text-xs font-medium text-slate-600">
          <span className="block mb-1">{t('expenses.filter_to')}</span>
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className={inputClass}
          />
        </label>
        <button
          type="button"
          onClick={clearFilters}
          className="bg-slate-100 text-slate-700 rounded-md px-3 py-2 text-sm font-medium hover:bg-slate-200"
        >
          {t('expenses.clear_filters')}
        </button>
      </div>

      {expenses.isLoading && (
        <p className="text-sm text-slate-600">{t('expenses.loading')}</p>
      )}
      {expenses.isError && (
        <p className="text-sm text-red-600">
          {t('expenses.error')}: {extractErrorMessage(expenses.error)}
        </p>
      )}
      {expenses.data && expenses.data.items.length === 0 && (
        <p className="text-sm text-slate-600">{t('expenses.empty')}</p>
      )}

      {expenses.data && expenses.data.items.length > 0 && (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 sticky top-0">
              <tr>
                <th className="text-left px-4 py-2 font-medium">{t('expenses.col_date')}</th>
                <th className="text-left px-4 py-2 font-medium">{t('expenses.col_job')}</th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_supplier')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_category')}
                </th>
                <th className="text-right px-4 py-2 font-medium">
                  {t('expenses.col_amount')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_status')}
                </th>
              </tr>
            </thead>
            <tbody>
              {expenses.data.items.map((exp) => (
                <tr
                  key={exp.expense_id}
                  className="border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="px-4 py-2 text-slate-700">
                    <Link
                      to={`/expenses/${exp.expense_id}`}
                      className="text-slate-900 hover:underline"
                    >
                      {exp.expense_date}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-700">
                    {jobMap.get(exp.job_id) ?? exp.job_id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-2 text-slate-700">
                    {exp.supplier_id
                      ? (supplierMap.get(exp.supplier_id) ?? exp.supplier_id.slice(0, 8))
                      : (exp.description ?? '—')}
                  </td>
                  <td className="px-4 py-2 text-slate-700">
                    {exp.category_id ? (categoryMap.get(exp.category_id) ?? '—') : '—'}
                  </td>
                  <td className="px-4 py-2 text-slate-900 text-right font-mono">
                    {exp.amount_inc_gst}
                  </td>
                  <td className="px-4 py-2">{renderStatus(exp.review_status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
