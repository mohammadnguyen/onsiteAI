import type { ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMe } from '../api/hooks/useAuth'

export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe()
  const { t } = useTranslation()

  if (me.isLoading) {
    return <p className="p-6 text-slate-500">{t('common.loading')}</p>
  }

  if (!me.data) {
    return <Navigate to="/login" replace />
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
