import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useRejectReview,
  useResolveReview,
  useReviewQueue,
  useReviewQueueItem,
} from '../api/hooks/useReviewQueue'
import { useCategories, useJobs } from '../api/hooks/useJobs'
import { useSuppliers } from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type ReviewQueueStatus = components['schemas']['ReviewQueueStatus']
type ReviewReasonCode = components['schemas']['ReviewReasonCode']
type ExpenseUpdate = components['schemas']['ExpenseUpdate']
type ExpenseDetailPublic = components['schemas']['ExpenseDetailPublic']
type ReceiptStatus = components['schemas']['ReceiptStatus']

const REASON_COLOR: Record<ReviewReasonCode, string> = {
  amount_uncertain: 'bg-amber-100 text-amber-800',
  unsupported_currency: 'bg-rose-100 text-rose-800',
  job_uncertain: 'bg-sky-100 text-sky-800',
  supplier_uncertain: 'bg-violet-100 text-violet-800',
  category_uncertain: 'bg-teal-100 text-teal-800',
  duplicate_suspected: 'bg-red-100 text-red-800',
}

export function ReviewQueue() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<ReviewQueueStatus>('open')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const queue = useReviewQueue(status)
  const detail = useReviewQueueItem(selectedId ?? undefined)
  const resolve = useResolveReview()
  const reject = useRejectReview()

  const jobs = useJobs()
  const categories = useCategories()
  const suppliers = useSuppliers()

  const jobMap = useMemo(() => {
    const m = new Map<string, string>()
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name))
    return m
  }, [jobs.data])

  // edit form state (syncs from detail)
  const [jobId, setJobId] = useState('')
  const [supplierId, setSupplierId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [amountIncGst, setAmountIncGst] = useState('')
  const [amountExGst, setAmountExGst] = useState('')
  const [gstAmount, setGstAmount] = useState('')
  const [description, setDescription] = useState('')
  const [notes, setNotes] = useState('')
  const [receiptStatus, setReceiptStatus] = useState<ReceiptStatus>('no_receipt')
  const [resolutionNotes, setResolutionNotes] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  // Populate form when a new detail loads
  useEffect(() => {
    if (!detail.data) return
    const e = detail.data.expense
    setSupplierId(e.supplier_id ?? '')
    setCategoryId(e.category_id ?? '')
    setAmountIncGst(e.amount_inc_gst)
    setAmountExGst(e.amount_ex_gst)
    setGstAmount(e.gst_amount)
    setDescription(e.description ?? '')
    setNotes(e.notes ?? '')
    setReceiptStatus(e.receipt_status)
    setJobId(e.job_id)
    setResolutionNotes('')
    setFormError(null)
  }, [detail.data])

  const reasonLabel = (code: ReviewReasonCode) =>
    t(`review_reason.${code}` as const)

  const handleResolve = async () => {
    if (!selectedId) return
    const patch: ExpenseUpdate = {
      supplier_id: supplierId || null,
      category_id: categoryId || null,
      amount_inc_gst: amountIncGst || null,
      amount_ex_gst: amountExGst || null,
      gst_amount: gstAmount || null,
      description: description || null,
      notes: notes || null,
      receipt_status: receiptStatus,
    }
    try {
      await resolve.mutateAsync({
        reviewId: selectedId,
        body: { expense_patch: patch, notes: resolutionNotes || null },
      })
      setSelectedId(null)
      setFormError(null)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  const handleReject = async () => {
    if (!selectedId) return
    try {
      await reject.mutateAsync({
        reviewId: selectedId,
        body: { notes: resolutionNotes || null },
      })
      setSelectedId(null)
      setFormError(null)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">{t('review.title')}</h1>
        <label className="text-sm text-slate-700">
          {t('review.filter_status')}:
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as ReviewQueueStatus)
              setSelectedId(null)
            }}
            className="ml-2 border border-slate-300 rounded-md px-2 py-1 text-sm"
          >
            <option value="open">{t('review.status_open')}</option>
            <option value="resolved">{t('review.status_resolved')}</option>
            <option value="rejected">{t('review.status_rejected')}</option>
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <aside className="lg:col-span-2">
          {queue.isLoading && (
            <p className="text-sm text-slate-600">{t('review.loading')}</p>
          )}
          {queue.isError && (
            <p className="text-sm text-red-600">
              {t('review.error')}: {extractErrorMessage(queue.error)}
            </p>
          )}
          {queue.data && queue.data.length === 0 && (
            <p className="text-sm text-slate-600">{t('review.no_items')}</p>
          )}
          {queue.data && queue.data.length > 0 && (
            <ul className="space-y-2">
              {queue.data.map((item) => {
                const active = item.review_id === selectedId
                return (
                  <li key={item.review_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.review_id)}
                      className={`w-full text-left rounded-lg border p-3 transition-colors ${
                        active
                          ? 'border-slate-900 bg-white shadow-sm'
                          : 'border-slate-200 bg-white hover:border-slate-400'
                      }`}
                    >
                      <div className="text-xs text-slate-500 mb-1">
                        {item.opened_at.slice(0, 10)} ·{' '}
                        {item.expense_id.slice(0, 8)}
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {item.review_reasons.map((r) => (
                          <span
                            key={r}
                            className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium ${REASON_COLOR[r]}`}
                          >
                            {reasonLabel(r)}
                          </span>
                        ))}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </aside>

        <section className="lg:col-span-3 bg-white rounded-lg border border-slate-200 p-6 min-h-[300px]">
          {!selectedId && (
            <p className="text-sm text-slate-600">{t('review.select_hint')}</p>
          )}
          {selectedId && detail.isLoading && (
            <p className="text-sm text-slate-600">{t('common.loading')}</p>
          )}
          {selectedId && detail.isError && (
            <p className="text-sm text-red-600">
              {t('common.error')}: {extractErrorMessage(detail.error)}
            </p>
          )}
          {selectedId && detail.data && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-2">
                {detail.data.review_reasons.map((r) => (
                  <span
                    key={r}
                    className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${REASON_COLOR[r]}`}
                  >
                    {reasonLabel(r)}
                  </span>
                ))}
                <span className="ml-auto text-xs text-slate-500">
                  {t('review.opened_at')}: {detail.data.opened_at}
                </span>
              </div>

              {detail.data.expense.raw_input_text && (
                <div>
                  <div className="text-xs font-medium text-slate-500 uppercase mb-1">
                    {t('review.raw_input')}
                  </div>
                  <pre className="text-xs text-slate-800 bg-slate-50 border border-slate-200 rounded-md p-3 whitespace-pre-wrap font-mono">
                    {detail.data.expense.raw_input_text}
                  </pre>
                </div>
              )}

              <div>
                <div className="text-xs font-medium text-slate-500 uppercase mb-2">
                  {t('review.parse_breakdown')}
                </div>
                <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
                  <KV
                    label={t('review.amount_conf')}
                    value={detail.data.expense.amount_inc_gst}
                  />
                  <KV
                    label={t('review.job_conf')}
                    value={
                      jobMap.get(detail.data.expense.job_id) ??
                      detail.data.expense.job_id.slice(0, 8)
                    }
                  />
                  <KV
                    label={t('review.supplier_conf')}
                    value={detail.data.expense.supplier?.supplier_name ?? '—'}
                  />
                  <KV
                    label={t('review.category_conf')}
                    value={detail.data.expense.category?.category_name ?? '—'}
                  />
                  <KV
                    label={t('review.confidence_score')}
                    value={detail.data.expense.confidence_score ?? '—'}
                  />
                </dl>
              </div>

              {detail.data.duplicate_of && (
                <DuplicatePanel
                  label={t('review.duplicate_of')}
                  expense={detail.data.duplicate_of}
                />
              )}

              <div>
                <div className="text-xs font-medium text-slate-500 uppercase mb-2">
                  {t('review.edit_patch')}
                </div>
                {/* job_id is intentionally omitted here — the Phase 2
                    backend ExpenseUpdate schema does not allow job
                    reassignment. If `job_uncertain` fires on an item
                    whose resolved job is actually wrong, the admin
                    should Reject the item and have the contributor
                    resubmit with the correct alias/job. Tracked as
                    Phase 2 tech debt in the Batch 4a report. */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label={t('expense.job')}>
                    <div className={inputClass + ' bg-slate-50 text-slate-500'}>
                      {jobs.data?.find((j) => j.job_id === jobId)?.job_name ?? '—'}
                    </div>
                  </Field>
                  <Field label={t('expense.supplier')}>
                    <select
                      value={supplierId}
                      onChange={(e) => setSupplierId(e.target.value)}
                      className={inputClass}
                    >
                      <option value="">—</option>
                      {suppliers.data?.map((s) => (
                        <option key={s.supplier_id} value={s.supplier_id}>
                          {s.supplier_name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t('expense.category')}>
                    <select
                      value={categoryId}
                      onChange={(e) => setCategoryId(e.target.value)}
                      className={inputClass}
                    >
                      <option value="">—</option>
                      {categories.data?.map((c) => (
                        <option key={c.category_id} value={c.category_id}>
                          {c.category_name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t('expense.amount_inc_gst')}>
                    <input
                      type="number"
                      step="0.01"
                      value={amountIncGst}
                      onChange={(e) => setAmountIncGst(e.target.value)}
                      className={inputClass}
                    />
                  </Field>
                  <Field label={t('expense.amount_ex_gst')}>
                    <input
                      type="number"
                      step="0.01"
                      value={amountExGst}
                      onChange={(e) => setAmountExGst(e.target.value)}
                      className={inputClass}
                    />
                  </Field>
                  <Field label={t('expense.gst')}>
                    <input
                      type="number"
                      step="0.01"
                      value={gstAmount}
                      onChange={(e) => setGstAmount(e.target.value)}
                      className={inputClass}
                    />
                  </Field>
                  <Field label={t('expense.receipt_status')}>
                    <select
                      value={receiptStatus}
                      onChange={(e) => setReceiptStatus(e.target.value as ReceiptStatus)}
                      className={inputClass}
                    >
                      <option value="no_receipt">
                        {t('expenses.receipt_no_receipt')}
                      </option>
                      <option value="expected_later">
                        {t('expenses.receipt_expected_later')}
                      </option>
                    </select>
                  </Field>
                </div>
                <div className="mt-3">
                  <Field label={t('expense.description')}>
                    <textarea
                      rows={2}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      className={inputClass}
                    />
                  </Field>
                </div>
                <div className="mt-3">
                  <Field label={t('expense.notes')}>
                    <textarea
                      rows={2}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      className={inputClass}
                    />
                  </Field>
                </div>
              </div>

              <Field label={t('review.resolution_notes')}>
                <textarea
                  rows={2}
                  value={resolutionNotes}
                  onChange={(e) => setResolutionNotes(e.target.value)}
                  className={inputClass}
                />
              </Field>

              {formError && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                  {formError}
                </div>
              )}

              {detail.data.status === 'open' && (
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={handleReject}
                    disabled={reject.isPending}
                    className="bg-red-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-red-700 disabled:opacity-50"
                  >
                    {reject.isPending ? t('review.rejecting') : t('review.reject')}
                  </button>
                  <button
                    type="button"
                    onClick={handleResolve}
                    disabled={resolve.isPending}
                    className="bg-emerald-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {resolve.isPending ? t('review.resolving') : t('review.resolve')}
                  </button>
                </div>
              )}
              {detail.data.status !== 'open' && detail.data.resolution_notes && (
                <div>
                  <div className="text-xs font-medium text-slate-500 uppercase mb-1">
                    {t('review.resolution_notes')}
                  </div>
                  <div className="text-sm text-slate-800 whitespace-pre-wrap">
                    {detail.data.resolution_notes}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </AppShell>
  )
}

function DuplicatePanel({
  label,
  expense,
}: {
  label: string
  expense: ExpenseDetailPublic
}) {
  return (
    <div className="border border-amber-200 bg-amber-50 rounded-md p-3">
      <div className="text-xs font-medium text-amber-800 uppercase mb-2">{label}</div>
      <dl className="grid grid-cols-2 gap-2 text-xs">
        <KV label="Date" value={expense.expense_date} />
        <KV label="Amount" value={expense.amount_inc_gst} />
        <KV
          label="Supplier"
          value={expense.supplier?.supplier_name ?? '—'}
        />
        <KV
          label="Category"
          value={expense.category?.category_name ?? '—'}
        />
      </dl>
      {expense.description && (
        <div className="mt-2 text-xs text-slate-700">{expense.description}</div>
      )}
    </div>
  )
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500 uppercase">{label}</dt>
      <dd className="text-slate-900">{value}</dd>
    </div>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-slate-600 mb-1">{label}</span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:bg-slate-100 disabled:text-slate-500'
