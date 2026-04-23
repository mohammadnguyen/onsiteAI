import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useExpenses, type ExpenseFilters } from '../api/hooks/useExpenses'
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
