import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useLogin } from '../api/hooks/useAuth'
import { extractErrorMessage } from '../api/client'

export function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await login.mutateAsync({ email, password })
      navigate('/', { replace: true })
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="bg-white rounded-lg shadow-md p-8 w-full max-w-sm space-y-4"
      >
        <h1 className="text-xl font-semibold text-slate-900 text-center">{t('login.title')}</h1>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1" htmlFor="email">
            {t('login.email')}
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1" htmlFor="password">
            {t('login.password')}
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500"
          />
        </div>
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
            {t('common.error')}: {error}
          </div>
        )}
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full bg-slate-900 text-white rounded-md py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
        >
          {login.isPending ? t('common.loading') : t('login.submit')}
        </button>
      </form>
    </div>
  )
}
