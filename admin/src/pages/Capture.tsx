import { useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useCreateExpense } from '../api/hooks/useExpenses'
import { useJobs, useCategories } from '../api/hooks/useJobs'
import { useSuppliers } from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type ExpenseCreateInput = components['schemas']['ExpenseCreate-Input']
type ExpenseCreateResponse = components['schemas']['ExpenseCreateResponse']
type ReviewReasonCode = components['schemas']['ReviewReasonCode']
type ReceiptStatus = components['schemas']['ReceiptStatus']

const REASON_COLOR: Record<ReviewReasonCode, string> = {
  amount_uncertain: 'bg-amber-100 text-amber-800',
  unsupported_currency: 'bg-rose-100 text-rose-800',
  job_uncertain: 'bg-sky-100 text-sky-800',
  supplier_uncertain: 'bg-violet-100 text-violet-800',
  category_uncertain: 'bg-teal-100 text-teal-800',
  duplicate_suspected: 'bg-red-100 text-red-800',
}

function todayIso(): string {
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

export function Capture() {
  const { t } = useTranslation()
  const createExpense = useCreateExpense()
  const jobs = useJobs()
  const categories = useCategories()
  const suppliers = useSuppliers()

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [rawInputText, setRawInputText] = useState('')
  const [receiptLater, setReceiptLater] = useState(false)
  const [expenseDate, setExpenseDate] = useState<string>(todayIso())
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [jobId, setJobId] = useState('')
  const [supplierId, setSupplierId] = useState('')
  const [amountIncGst, setAmountIncGst] = useState('')
  const [description, setDescription] = useState('')
  const [notes, setNotes] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [result, setResult] = useState<ExpenseCreateResponse | null>(null)

  const resetForm = () => {
    setRawInputText('')
    setReceiptLater(false)
    setExpenseDate(todayIso())
    setAdvancedOpen(false)
    setJobId('')
    setSupplierId('')
    setAmountIncGst('')
    setDescription('')
    setNotes('')
    setFormError(null)
    setResult(null)
    // Return focus to textarea after the DOM paints the form again.
    setTimeout(() => {
      textareaRef.current?.focus()
      textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 0)
  }

  const onSubmit = async (ev: FormEvent) => {
    ev.preventDefault()
    setFormError(null)
    const body: ExpenseCreateInput = {
      raw_input_text: rawInputText || null,
      job_id: jobId || null,
      supplier_id: supplierId || null,
      amount_inc_gst: amountIncGst || null,
      description: description || null,
      notes: notes || null,
      expense_date: expenseDate || null,
      expense_type: 'supplier_expense',
      payment_method: 'unknown',
      receipt_status: (receiptLater ? 'expected_later' : 'no_receipt') as ReceiptStatus,
    }
    try {
      const resp = await createExpense.mutateAsync(body)
      setResult(resp)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  const jobNameOf = (id: string) =>
    jobs.data?.find((j) => j.job_id === id)?.job_name ?? id.slice(0, 8)
  const supplierNameOf = (id: string | null) =>
    id ? (suppliers.data?.find((s) => s.supplier_id === id)?.supplier_name ?? id.slice(0, 8)) : null
  const categoryNameOf = (id: string | null) =>
    id ? (categories.data?.find((c) => c.category_id === id)?.category_name ?? '—') : '—'

  return (
    <AppShell>
      <div className="max-w-2xl mx-auto">
        <h1 className="text-2xl font-semibold text-slate-900 mb-6">
          {t('capture.title')}
        </h1>

        {!result && (
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <textarea
                ref={textareaRef}
                rows={6}
                autoFocus
                value={rawInputText}
                onChange={(e) => setRawInputText(e.target.value)}
                placeholder={t('capture.textarea_placeholder')}
                className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-slate-500"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="receipt-later"
                type="checkbox"
                checked={receiptLater}
                onChange={(e) => setReceiptLater(e.target.checked)}
                className="rounded border-slate-300"
              />
              <label htmlFor="receipt-later" className="text-sm text-slate-700">
                {t('capture.receipt_later')}
              </label>
            </div>

            <div>
              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                className="text-sm text-slate-600 hover:text-slate-900"
              >
                {advancedOpen ? '▾' : '▸'} {t('capture.advanced')}
              </button>
            </div>

            {advancedOpen && (
              <div className="bg-slate-50 border border-slate-200 rounded-md p-4 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="block text-sm">
                    <span className="block text-xs font-medium text-slate-600 mb-1">
                      {t('capture.job')}
                    </span>
                    <select
                      value={jobId}
                      onChange={(e) => setJobId(e.target.value)}
                      className={inputClass}
                    >
                      <option value="">—</option>
                      {jobs.data?.map((j) => (
                        <option key={j.job_id} value={j.job_id}>
                          {j.job_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="block text-xs font-medium text-slate-600 mb-1">
                      {t('capture.supplier')}
                    </span>
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
                  </label>
                  <label className="block text-sm">
                    <span className="block text-xs font-medium text-slate-600 mb-1">
                      {t('capture.amount')}
                    </span>
                    <input
                      type="number"
                      step="0.01"
                      value={amountIncGst}
                      onChange={(e) => setAmountIncGst(e.target.value)}
                      className={inputClass}
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="block text-xs font-medium text-slate-600 mb-1">
                      {t('expense.date')}
                    </span>
                    <input
                      type="date"
                      value={expenseDate}
                      onChange={(e) => setExpenseDate(e.target.value)}
                      className={inputClass}
                    />
                  </label>
                </div>
                <label className="block text-sm">
                  <span className="block text-xs font-medium text-slate-600 mb-1">
                    {t('capture.description')}
                  </span>
                  <textarea
                    rows={2}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className={inputClass}
                  />
                </label>
                <label className="block text-sm">
                  <span className="block text-xs font-medium text-slate-600 mb-1">
                    {t('capture.notes')}
                  </span>
                  <textarea
                    rows={2}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    className={inputClass}
                  />
                </label>
              </div>
            )}

            {formError && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                {formError}
              </div>
            )}

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={createExpense.isPending || !rawInputText.trim()}
                className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
              >
                {createExpense.isPending ? t('common.loading') : t('capture.submit')}
              </button>
            </div>
          </form>
        )}

        {result && (
          <ResultView
            result={result}
            onReset={resetForm}
            jobNameOf={jobNameOf}
            supplierNameOf={supplierNameOf}
            categoryNameOf={categoryNameOf}
            reasonColor={REASON_COLOR}
          />
        )}
      </div>
    </AppShell>
  )
}

function ResultView({
  result,
  onReset,
  jobNameOf,
  supplierNameOf,
  categoryNameOf,
  reasonColor,
}: {
  result: ExpenseCreateResponse
  onReset: () => void
  jobNameOf: (id: string) => string
  supplierNameOf: (id: string | null) => string | null
  categoryNameOf: (id: string | null) => string
  reasonColor: Record<ReviewReasonCode, string>
}) {
  const { t } = useTranslation()
  const { expense } = result
  const isReviewed = expense.review_status === 'reviewed'
  const reasons = result.parse?.review_reasons ?? []

  return (
    <div className="space-y-4">
      <div
        className={`rounded-md border px-4 py-3 ${
          isReviewed
            ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
            : 'bg-amber-50 border-amber-200 text-amber-900'
        }`}
      >
        <div className="font-medium">
          {isReviewed
            ? t('capture.result_saved')
            : t('capture.result_pending_review')}
        </div>
        {reasons.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {reasons.map((r) => (
              <span
                key={r}
                className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${reasonColor[r]}`}
              >
                {t(`review_reason.${r}` as const)}
              </span>
            ))}
          </div>
        )}
      </div>

      <dl className="bg-white rounded-lg border border-slate-200 p-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <Info label={t('capture.amount')} value={expense.amount_inc_gst} />
        <Info label={t('capture.job')} value={jobNameOf(expense.job_id)} />
        <Info
          label={t('capture.supplier')}
          value={supplierNameOf(expense.supplier_id) ?? expense.description ?? '—'}
        />
        <Info
          label={t('expense.category')}
          value={categoryNameOf(expense.category_id)}
        />
        <Info label={t('expense.payment')} value={expense.payment_method} />
        <Info label={t('expense.date')} value={expense.expense_date} />
      </dl>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={onReset}
          className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800"
        >
          {t('capture.new_expense')}
        </button>
      </div>
    </div>
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

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
