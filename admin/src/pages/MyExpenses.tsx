import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useExpenses } from '../api/hooks/useExpenses'
import { useJobs } from '../api/hooks/useJobs'
import { useSuppliers } from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type ExpensePublic = components['schemas']['ExpensePublic']
type ReviewStatus = components['schemas']['ReviewStatus']

export function MyExpenses() {
  const { t } = useTranslation()
  const expenses = useExpenses({ mine: 1 })
  const jobs = useJobs()
  const suppliers = useSuppliers()

  const jobMap = useMemo(() => {
    const m = new Map<string, string>()
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name))
    return m
  }, [jobs.data])

  const supplierMap = useMemo(() => {
    const m = new Map<string, string>()
    suppliers.data?.forEach((s) => m.set(s.supplier_id, s.supplier_name))
    return m
  }, [suppliers.data])

  const { pending, reviewed } = useMemo(() => {
    const items = expenses.data?.items ?? []
    return {
      pending: items.filter((e) => e.review_status === 'pending'),
      reviewed: items.filter((e) => e.review_status !== 'pending'),
    }
  }, [expenses.data])

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

  const supplierOrDescription = (e: ExpensePublic) => {
    if (e.supplier_id) return supplierMap.get(e.supplier_id) ?? e.supplier_id.slice(0, 8)
    return e.description ?? '—'
  }

  const renderSection = (
    title: string,
    items: ExpensePublic[],
    emptyKey = 'my_expenses.empty',
  ) => (
    <section className="mb-8">
      <h2 className="text-lg font-semibold text-slate-800 mb-3">{title}</h2>
      {items.length === 0 ? (
        <p className="text-sm text-slate-600">{t(emptyKey)}</p>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_date')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_job')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('expenses.col_supplier')}
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
              {items.map((e) => (
                <tr key={e.expense_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2 text-slate-700">
                    <Link
                      to={`/expenses/${e.expense_id}`}
                      className="text-slate-900 hover:underline"
                    >
                      {e.expense_date}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-700">
                    {jobMap.get(e.job_id) ?? e.job_id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-2 text-slate-700">
                    {supplierOrDescription(e)}
                  </td>
                  <td className="px-4 py-2 text-slate-900 text-right font-mono">
                    {e.amount_inc_gst}
                  </td>
                  <td className="px-4 py-2">{renderStatus(e.review_status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )

  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900 mb-6">
        {t('my_expenses.title')}
      </h1>

      {expenses.isLoading && (
        <p className="text-sm text-slate-600">{t('common.loading')}</p>
      )}
      {expenses.isError && (
        <p className="text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(expenses.error)}
        </p>
      )}

      {expenses.data && (
        <>
          {renderSection(t('my_expenses.pending_section'), pending)}
          {renderSection(t('my_expenses.reviewed_section'), reviewed)}
        </>
      )}
    </AppShell>
  )
}
