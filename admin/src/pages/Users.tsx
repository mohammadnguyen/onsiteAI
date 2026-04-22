import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useInviteUser, useUpdateUser, useUsers } from '../api/hooks/useUsers'
import { AppShell } from '../components/AppShell'
import { Modal } from '../components/Modal'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type UserRole = components['schemas']['UserRole']
type LanguageCode = components['schemas']['LanguageCode']

export function Users() {
  const { t } = useTranslation()
  const users = useUsers()
  const invite = useInviteUser()
  const updateUser = useUpdateUser()

  const [open, setOpen] = useState(false)
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<UserRole>('contributor')
  const [initialPassword, setInitialPassword] = useState('')
  const [languagePreference, setLanguagePreference] = useState<LanguageCode>('en')
  const [formError, setFormError] = useState<string | null>(null)

  const resetForm = () => {
    setFullName('')
    setEmail('')
    setRole('contributor')
    setInitialPassword('')
    setLanguagePreference('en')
    setFormError(null)
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    try {
      await invite.mutateAsync({
        full_name: fullName,
        email,
        role,
        initial_password: initialPassword,
        language_preference: languagePreference,
      })
      resetForm()
      setOpen(false)
    } catch (err) {
      setFormError(extractErrorMessage(err))
    }
  }

  const onDeactivate = async (userId: string) => {
    if (!window.confirm(t('users.confirm_deactivate'))) return
    try {
      await updateUser.mutateAsync({ userId, body: { is_active: false } })
    } catch (err) {
      window.alert(extractErrorMessage(err))
    }
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">{t('users.title')}</h1>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800"
        >
          {t('users.invite')}
        </button>
      </div>

      {users.isLoading && <p className="text-sm text-slate-600">{t('common.loading')}</p>}
      {users.isError && (
        <p className="text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(users.error)}
        </p>
      )}

      {users.data && (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left px-4 py-2 font-medium">{t('users.name')}</th>
                <th className="text-left px-4 py-2 font-medium">{t('users.email')}</th>
                <th className="text-left px-4 py-2 font-medium">{t('users.role')}</th>
                <th className="text-left px-4 py-2 font-medium">{t('users.active')}</th>
                <th className="text-right px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => (
                <tr key={u.user_id} className="border-t border-slate-100">
                  <td className="px-4 py-2 text-slate-900">{u.full_name}</td>
                  <td className="px-4 py-2 text-slate-700">{u.email}</td>
                  <td className="px-4 py-2 text-slate-700">
                    {u.role === 'admin' ? t('users.role_admin') : t('users.role_contributor')}
                  </td>
                  <td className="px-4 py-2 text-slate-700">{u.is_active ? '✓' : '—'}</td>
                  <td className="px-4 py-2 text-right">
                    {u.is_active && (
                      <button
                        type="button"
                        onClick={() => onDeactivate(u.user_id)}
                        disabled={updateUser.isPending}
                        className="text-sm text-red-700 hover:underline disabled:opacity-50"
                      >
                        {t('users.deactivate')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={t('users.invite')}>
        <form onSubmit={onSubmit} className="space-y-3">
          <Field label={t('users.name')} required>
            <input
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('users.email')} required>
            <input
              required
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('users.role')} required>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className={inputClass}
            >
              <option value="contributor">{t('users.role_contributor')}</option>
              <option value="admin">{t('users.role_admin')}</option>
            </select>
          </Field>
          <Field label={t('users.initial_password')} required>
            <input
              required
              type="password"
              value={initialPassword}
              onChange={(e) => setInitialPassword(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label={t('users.language_preference')}>
            <select
              value={languagePreference}
              onChange={(e) => setLanguagePreference(e.target.value as LanguageCode)}
              className={inputClass}
            >
              <option value="en">{t('lang.english')}</option>
              <option value="zh">{t('lang.chinese')}</option>
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
            <button type="submit" disabled={invite.isPending} className={btnPrimary}>
              {invite.isPending ? t('common.loading') : t('common.save')}
            </button>
          </div>
        </form>
      </Modal>
    </AppShell>
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
