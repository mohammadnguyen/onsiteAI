import { Fragment, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useAddSupplierAlias,
  useCreateSupplier,
  useSuppliers,
  useUpdateSupplier,
} from '../api/hooks/useSuppliers'
import { AppShell } from '../components/AppShell'
import { Modal } from '../components/Modal'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type LanguageCode = components['schemas']['LanguageCode']

export function Suppliers() {
  const { t } = useTranslation()
  const suppliers = useSuppliers()
  const createSupplier = useCreateSupplier()
  const updateSupplier = useUpdateSupplier()
  const addAlias = useAddSupplierAlias()

  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newActive, setNewActive] = useState(true)
  const [formError, setFormError] = useState<string | null>(null)

  // per-row inline alias form state (keyed by supplier_id)
  const [aliasOpenId, setAliasOpenId] = useState<string | null>(null)
  const [aliasText, setAliasText] = useState('')
  const [aliasLang, setAliasLang] = useState<LanguageCode | ''>('')
  const [aliasError, setAliasError] = useState<string | null>(null)

  const resetNew = () => {
    setNewName('')
    setNewActive(true)
    setFormError(null)
  }

  const submitNew = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    try {
      await createSupplier.mutateAsync({
        supplier_name: newName,
        is_active: newActive,
      })
      resetNew()
      setOpen(false)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  const toggleActive = async (supplierId: string, currentlyActive: boolean) => {
    try {
      await updateSupplier.mutateAsync({
        supplierId,
        body: { is_active: !currentlyActive },
      })
    } catch (err) {
      window.alert(extractErrorMessage(err))
    }
  }

  const openAliasForm = (supplierId: string) => {
    setAliasOpenId(supplierId)
    setAliasText('')
    setAliasLang('')
    setAliasError(null)
  }

  const submitAlias = async (e: FormEvent, supplierId: string) => {
    e.preventDefault()
    setAliasError(null)
    try {
      await addAlias.mutateAsync({
        supplierId,
        body: {
          alias_text: aliasText,
          language_code: aliasLang === '' ? null : aliasLang,
        },
      })
      setAliasOpenId(null)
      setAliasText('')
      setAliasLang('')
    } catch (err) {
      setAliasError(extractErrorMessage(err))
    }
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">{t('suppliers.title')}</h1>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800"
        >
          {t('suppliers.new')}
        </button>
      </div>

      {suppliers.isLoading && (
        <p className="text-sm text-slate-600">{t('suppliers.loading')}</p>
      )}
      {suppliers.isError && (
        <p className="text-sm text-red-600">
          {t('suppliers.error')}: {extractErrorMessage(suppliers.error)}
        </p>
      )}
      {suppliers.data && suppliers.data.length === 0 && (
        <p className="text-sm text-slate-600">{t('suppliers.none')}</p>
      )}

      {suppliers.data && suppliers.data.length > 0 && (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left px-4 py-2 font-medium">
                  {t('suppliers.name')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('suppliers.normalized')}
                </th>
                <th className="text-left px-4 py-2 font-medium">
                  {t('suppliers.status')}
                </th>
                <th className="text-right px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {suppliers.data.map((s) => (
                <Fragment key={s.supplier_id}>
                  <tr className="border-t border-slate-100">
                    <td className="px-4 py-2 text-slate-900">{s.supplier_name}</td>
                    <td className="px-4 py-2 text-slate-500 font-mono text-xs">
                      {s.supplier_normalized}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                          s.is_active
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-slate-200 text-slate-700'
                        }`}
                      >
                        {s.is_active ? t('suppliers.active') : t('suppliers.inactive')}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right space-x-3">
                      <button
                        type="button"
                        onClick={() => openAliasForm(s.supplier_id)}
                        className="text-sm text-slate-700 hover:underline"
                      >
                        + {t('suppliers.add_alias')}
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleActive(s.supplier_id, s.is_active)}
                        disabled={updateSupplier.isPending}
                        className={`text-sm hover:underline disabled:opacity-50 ${
                          s.is_active ? 'text-red-700' : 'text-emerald-700'
                        }`}
                      >
                        {s.is_active
                          ? t('suppliers.deactivate')
                          : t('suppliers.activate')}
                      </button>
                    </td>
                  </tr>
                  {aliasOpenId === s.supplier_id && (
                    <tr className="bg-slate-50 border-t border-slate-100">
                      <td colSpan={4} className="px-4 py-3">
                        <form
                          onSubmit={(e) => submitAlias(e, s.supplier_id)}
                          className="flex flex-wrap gap-2 items-end"
                        >
                          <label className="flex-1 min-w-[200px]">
                            <span className="block text-xs font-medium text-slate-600 mb-1">
                              {t('suppliers.alias_text')}
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
                              {t('suppliers.language')}
                            </span>
                            <select
                              value={aliasLang}
                              onChange={(e) =>
                                setAliasLang(e.target.value as LanguageCode | '')
                              }
                              className={inputClass}
                            >
                              <option value="">—</option>
                              <option value="en">EN</option>
                              <option value="zh">ZH</option>
                            </select>
                          </label>
                          <button
                            type="submit"
                            disabled={addAlias.isPending}
                            className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
                          >
                            {addAlias.isPending
                              ? t('common.loading')
                              : t('suppliers.add_alias')}
                          </button>
                          <button
                            type="button"
                            onClick={() => setAliasOpenId(null)}
                            className="bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200"
                          >
                            {t('common.cancel')}
                          </button>
                        </form>
                        {aliasError && (
                          <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                            {aliasError}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={t('suppliers.new')}>
        <form onSubmit={submitNew} className="space-y-3">
          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">
              {t('suppliers.name')}
              <span className="text-red-500 ml-0.5">*</span>
            </span>
            <input
              required
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={newActive}
              onChange={(e) => setNewActive(e.target.checked)}
              className="h-4 w-4"
            />
            <span className="text-sm text-slate-700">{t('suppliers.active')}</span>
          </label>
          {formError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
              {formError}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={createSupplier.isPending}
              className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {createSupplier.isPending ? t('common.loading') : t('common.save')}
            </button>
          </div>
        </form>
      </Modal>
    </AppShell>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'
