import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  useDeleteExpense,
  useExpense,
  useExpenseAudit,
  useUpdateExpense,
} from '../api/hooks/useExpenses'
import { useMe } from '../api/hooks/useAuth'
import { useCategories, useJobs } from '../api/hooks/useJobs'
import { useSuppliers } from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type ExpenseUpdate = components['schemas']['ExpenseUpdate']
type PaymentMethod = components['schemas']['PaymentMethod']
type ReceiptStatus = components['schemas']['ReceiptStatus']
type ReviewStatus = components['schemas']['ReviewStatus']
type ExpenseType = components['schemas']['ExpenseType']

type Tab = 'detail' | 'audit'

export function ExpenseDetail() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const me = useMe()
  const isAdmin = me.data?.role === 'admin'
  const expense = useExpense(id)
  const updateExpense = useUpdateExpense()
  const deleteExpense = useDeleteExpense()

  const jobs = useJobs()
  const categories = useCategories()
  const suppliers = useSuppliers()

  const [tab, setTab] = useState<Tab>('detail')
  const audit = useExpenseAudit(id, tab === 'audit')

  const [editMode, setEditMode] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // form fields — initialised on entering edit mode
  const [supplierId, setSupplierId] = useState<string>('')
  const [categoryId, setCategoryId] = useState<string>('')
  // Only amount_inc_gst is editable; the backend derives amount_ex_gst /
  // gst_amount from it + payment_method (see B-1/B-2). Sending a stale
  // ex/gst breakdown would violate the inc = ex + gst DB CHECK.
  const [amountIncGst, setAmountIncGst] = useState<string>('')
  const [description, setDescription] = useState<string>('')
  const [notes, setNotes] = useState<string>('')
  const [expenseDate, setExpenseDate] = useState<string>('')
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('unknown')
  const [receiptStatus, setReceiptStatus] = useState<ReceiptStatus>('no_receipt')
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>('pending')
  const [expenseType, setExpenseType] = useState<ExpenseType>('supplier_expense')
  const [reason, setReason] = useState<string>('')

  const beginEdit = () => {
    if (!expense.data) return
    const e = expense.data
    setSupplierId(e.supplier_id ?? '')
    setCategoryId(e.category_id ?? '')
    setAmountIncGst(e.amount_inc_gst)
    setDescription(e.description ?? '')
    setNotes(e.notes ?? '')
    setExpenseDate(e.expense_date)
    setPaymentMethod(e.payment_method)
    setReceiptStatus(e.receipt_status)
    setReviewStatus(e.review_status)
    setExpenseType(e.expense_type)
    setReason('')
    setFormError(null)
    setEditMode(true)
  }

  const submitEdit = async (ev: FormEvent) => {
    ev.preventDefault()
    if (!id) return
    setFormError(null)
    const body: ExpenseUpdate = {
      supplier_id: supplierId || null,
      category_id: categoryId || null,
      amount_inc_gst: amountIncGst || null,
      description: description || null,
      notes: notes || null,
      expense_date: expenseDate || null,
      payment_method: paymentMethod,
      receipt_status: receiptStatus,
      review_status: reviewStatus,
      expense_type: expenseType,
      reason: reason || null,
    }
    try {
      await updateExpense.mutateAsync({ expenseId: id, body })
      setEditMode(false)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  const onDelete = async () => {
    if (!id) return
    if (!window.confirm(t('expense.confirm_delete'))) return
    const userReason = window.prompt(t('expense.delete_reason')) ?? undefined
    try {
      await deleteExpense.mutateAsync({ expenseId: id, reason: userReason })
      navigate('/expenses', { replace: true })
    } catch (err) {
      window.alert(extractErrorMessage(err))
    }
  }

  const paymentLabel = (pm: PaymentMethod) =>
    pm === 'cash'
      ? t('expense.payment_cash')
      : pm === 'transfer'
        ? t('expense.payment_transfer')
        : t('expense.payment_unknown')

  const typeLabel = (et: ExpenseType) =>
    et === 'labour'
      ? t('expense.type_labour')
      : et === 'adjustment'
        ? t('expense.type_adjustment')
        : t('expense.type_supplier_expense')

  const receiptLabel = (rs: ReceiptStatus) =>
    rs === 'expected_later'
      ? t('expenses.receipt_expected_later')
      : t('expenses.receipt_no_receipt')

  const statusLabel = (rs: ReviewStatus) =>
    rs === 'pending'
      ? t('expenses.status_pending')
      : rs === 'reviewed'
        ? t('expenses.status_reviewed')
        : t('expenses.status_rejected')

  return (
    <AppShell>
      <Link to="/expenses" className="text-sm text-slate-600 hover:underline">
        &larr; {t('expense.back')}
      </Link>

      {expense.isLoading && (
        <p className="mt-4 text-sm text-slate-600">{t('common.loading')}</p>
      )}
      {expense.isError && (
        <p className="mt-4 text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(expense.error)}
        </p>
      )}
      {expense.data === null && (
        <p className="mt-4 text-sm text-slate-600">{t('expense.not_found')}</p>
      )}

      {expense.data && (
        <div className="mt-4 space-y-4">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-semibold text-slate-900">
              {expense.data.supplier?.supplier_name ??
                expense.data.description ??
                expense.data.amount_inc_gst}
            </h1>
            <div className="flex items-center gap-2">
              {!editMode && (
                <>
                  {/* Edit: admin always; contributor only when it's
                      their own expense AND it's still pending. The
                      backend enforces the same rule; this just avoids
                      a surprise 403 in the happy-path UX. */}
                  {(isAdmin ||
                    (me.data &&
                      expense.data.entered_by_user_id === me.data.user_id &&
                      expense.data.review_status === 'pending')) && (
                    <button type="button" onClick={beginEdit} className={btnSecondary}>
                      {t('expense.edit')}
                    </button>
                  )}
                  {/* Delete is admin-only (backend returns 403 for
                      contributors). */}
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={onDelete}
                      disabled={deleteExpense.isPending}
                      className="bg-red-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-red-700 disabled:opacity-50"
                    >
                      {t('expense.delete')}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          <div className="border-b border-slate-200 flex gap-2">
            <button
              type="button"
              onClick={() => setTab('detail')}
              className={tabClass(tab === 'detail')}
            >
              {t('expense.tab_detail')}
            </button>
            {/* Audit log is admin-only (backend gates GET /expenses/{id}/audit). */}
            {isAdmin && (
              <button
                type="button"
                onClick={() => setTab('audit')}
                className={tabClass(tab === 'audit')}
              >
                {t('expense.tab_audit')}
              </button>
            )}
          </div>

          {tab === 'detail' && !editMode && (
            <div className="bg-white rounded-lg border border-slate-200 p-6">
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                <Info
                  label={t('expense.amount_inc_gst')}
                  value={expense.data.amount_inc_gst}
                />
                <Info
                  label={t('expense.amount_ex_gst')}
                  value={expense.data.amount_ex_gst}
                />
                <Info label={t('expense.gst')} value={expense.data.gst_amount} />
                <Info
                  label={t('expense.expense_type')}
                  value={typeLabel(expense.data.expense_type)}
                />
                <Info label={t('expense.date')} value={expense.data.expense_date} />
                <Info
                  label={t('expense.payment')}
                  value={paymentLabel(expense.data.payment_method)}
                />
                <Info
                  label={t('expense.supplier')}
                  value={expense.data.supplier?.supplier_name ?? '—'}
                />
                <Info
                  label={t('expense.category')}
                  value={expense.data.category?.category_name ?? '—'}
                />
                <Info
                  label={t('expense.job')}
                  value={jobs.data?.find((j) => j.job_id === expense.data!.job_id)?.job_name ?? expense.data.job_id}
                />
                <Info
                  label={t('expense.review_status')}
                  value={statusLabel(expense.data.review_status)}
                />
                <Info
                  label={t('expense.receipt_status')}
                  value={receiptLabel(expense.data.receipt_status)}
                />
                <Info
                  label={t('expense.confidence')}
                  value={expense.data.confidence_score ?? '—'}
                />
                <Info
                  label={t('expense.duplicate_flag')}
                  value={expense.data.duplicate_flag ? t('expense.yes') : t('expense.no')}
                />
                {expense.data.duplicate_of_expense_id && (
                  <Info
                    label={t('expense.duplicate_of')}
                    value={expense.data.duplicate_of_expense_id}
                  />
                )}
              </dl>
              {(expense.data.description || expense.data.notes || expense.data.raw_input_text) && (
                <div className="mt-6 space-y-4">
                  {expense.data.description && (
                    <div>
                      <div className="text-xs font-medium text-slate-500 uppercase mb-1">
                        {t('expense.description')}
                      </div>
                      <div className="text-sm text-slate-900 whitespace-pre-wrap">
                        {expense.data.description}
                      </div>
                    </div>
                  )}
                  {expense.data.notes && (
                    <div>
                      <div className="text-xs font-medium text-slate-500 uppercase mb-1">
                        {t('expense.notes')}
                      </div>
                      <div className="text-sm text-slate-900 whitespace-pre-wrap">
                        {expense.data.notes}
                      </div>
                    </div>
                  )}
                  {expense.data.raw_input_text && (
                    <div>
                      <div className="text-xs font-medium text-slate-500 uppercase mb-1">
                        {t('expense.raw_input_text')}
                      </div>
                      <pre className="text-xs text-slate-800 bg-slate-50 border border-slate-200 rounded-md p-3 whitespace-pre-wrap font-mono">
                        {expense.data.raw_input_text}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {tab === 'detail' && editMode && (
            <form
              onSubmit={submitEdit}
              className="bg-white rounded-lg border border-slate-200 p-6 space-y-3"
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
                <Field label={t('expense.date')}>
                  <input
                    type="date"
                    value={expenseDate}
                    onChange={(e) => setExpenseDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label={t('expense.payment')}>
                  <select
                    value={paymentMethod}
                    onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod)}
                    className={inputClass}
                  >
                    <option value="unknown">{t('expense.payment_unknown')}</option>
                    <option value="cash">{t('expense.payment_cash')}</option>
                    <option value="transfer">{t('expense.payment_transfer')}</option>
                  </select>
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
                <Field label={t('expense.review_status')}>
                  <select
                    value={reviewStatus}
                    onChange={(e) => setReviewStatus(e.target.value as ReviewStatus)}
                    className={inputClass}
                  >
                    <option value="pending">{t('expenses.status_pending')}</option>
                    <option value="reviewed">{t('expenses.status_reviewed')}</option>
                    <option value="rejected">{t('expenses.status_rejected')}</option>
                  </select>
                </Field>
                <Field label={t('expense.expense_type')}>
                  <select
                    value={expenseType}
                    onChange={(e) => setExpenseType(e.target.value as ExpenseType)}
                    className={inputClass}
                  >
                    <option value="supplier_expense">
                      {t('expense.type_supplier_expense')}
                    </option>
                    <option value="labour">{t('expense.type_labour')}</option>
                    <option value="adjustment">{t('expense.type_adjustment')}</option>
                  </select>
                </Field>
              </div>
              <Field label={t('expense.description')}>
                <textarea
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className={inputClass}
                />
              </Field>
              <Field label={t('expense.notes')}>
                <textarea
                  rows={2}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className={inputClass}
                />
              </Field>
              <Field label={t('expense.reason')}>
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className={inputClass}
                />
              </Field>
              {formError && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                  {formError}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setEditMode(false)}
                  className={btnSecondary}
                >
                  {t('expense.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={updateExpense.isPending}
                  className={btnPrimary}
                >
                  {updateExpense.isPending ? t('common.loading') : t('expense.save')}
                </button>
              </div>
            </form>
          )}

          {tab === 'audit' && (
            <div className="bg-white rounded-lg border border-slate-200 p-6">
              {audit.isLoading && (
                <p className="text-sm text-slate-600">{t('common.loading')}</p>
              )}
              {audit.isError && (
                <p className="text-sm text-red-600">
                  {t('common.error')}: {extractErrorMessage(audit.error)}
                </p>
              )}
              {audit.data && audit.data.length === 0 && (
                <p className="text-sm text-slate-600">{t('expense.audit_no_entries')}</p>
              )}
              {audit.data && audit.data.length > 0 && (
                <ul className="space-y-3">
                  {audit.data.map((row) => (
                    <li
                      key={row.audit_id}
                      className="border border-slate-100 rounded-md p-3"
                    >
                      <div className="flex items-center justify-between text-xs text-slate-600 mb-2">
                        <span>
                          {t('expense.audit_edited_by')}: {row.edited_by_user_id.slice(0, 8)}
                        </span>
                        <span>{row.edited_at}</span>
                      </div>
                      {row.reason && (
                        <div className="text-sm text-slate-800 mb-2">
                          <span className="font-medium">{t('expense.reason')}:</span>{' '}
                          {row.reason}
                        </div>
                      )}
                      <div className="text-xs text-slate-500 uppercase mb-1">
                        {t('expense.audit_changed_fields')}
                      </div>
                      <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-2 whitespace-pre-wrap font-mono overflow-x-auto">
                        {JSON.stringify(row.changed_fields, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
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

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1">{label}</span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
const btnPrimary =
  'bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50'
const btnSecondary =
  'bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200'

function tabClass(active: boolean) {
  return `px-3 py-2 text-sm font-medium border-b-2 -mb-px ${
    active
      ? 'border-slate-900 text-slate-900'
      : 'border-transparent text-slate-600 hover:text-slate-900'
  }`
}
