import type { ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMe } from '../api/hooks/useAuth'
import { useAuthStore } from '../store/auth'

export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe()
  const accessToken = useAuthStore((s) => s.accessToken)
  const { t } = useTranslation()

  if (me.isLoading) {
    return <p className="p-6 text-slate-500">{t('common.loading')}</p>
  }

  // R25/M1: distinguish a genuine logged-out state from a transient /me
  // failure. No token → truly logged out → /login. Token present but /me
  // erroring (useMe retry exhausted on a network blip) → a valid admin
  // must NOT be bounced to /login; show a retry state instead.
  if (!me.data) {
    if (!accessToken) {
      return <Navigate to="/login" replace />
    }
    return (
      <main className="p-8 max-w-xl mx-auto">
        <h1 className="text-xl font-semibold text-slate-900">
          {t('common.error')}
        </h1>
        <button
          type="button"
          onClick={() => void me.refetch()}
          className="inline-block mt-4 px-4 py-2 rounded-md text-sm font-medium bg-slate-900 text-white hover:bg-slate-800"
        >
          {t('common.retry')}
        </button>
      </main>
    )
  }

  if (me.data.role !== 'admin') {
    return (
      <main className="p-8 max-w-xl mx-auto">
        <h1 className="text-xl font-semibold text-slate-900">
          {t('access_denied.title')}
        </h1>
        <p className="text-slate-600 mt-2">{t('access_denied.message')}</p>
        <Link
          to="/capture"
          className="inline-block mt-4 px-4 py-2 rounded-md text-sm font-medium bg-slate-900 text-white hover:bg-slate-800"
        >
          {t('access_denied.go_to_capture')}
        </Link>
      </main>
    )
  }

  return <>{children}</>
}
