import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { setLanguage } from '../i18n'
import { useLogout } from '../api/hooks/useAuth'

export function AppShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const logout = useLogout()

  const linkClasses = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium ${
      isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-200'
    }`

  const handleLogout = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200 px-6 py-3 flex items-center gap-4">
        <div className="text-lg font-semibold text-slate-900">SiteTracker</div>
        <nav className="flex gap-2">
          <NavLink to="/jobs" className={linkClasses}>
            {t('nav.jobs')}
          </NavLink>
          <NavLink to="/users" className={linkClasses}>
            {t('nav.users')}
          </NavLink>
          <NavLink to="/expenses" className={linkClasses}>
            {t('nav.expenses')}
          </NavLink>
          <NavLink to="/review-queue" className={linkClasses}>
            {t('nav.review_queue')}
          </NavLink>
          <NavLink to="/suppliers" className={linkClasses}>
            {t('nav.suppliers')}
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <label className="text-sm text-slate-600">
            {t('lang.label')}:
            <select
              className="ml-2 border border-slate-300 rounded-md px-2 py-1 text-sm"
              value={i18n.language.startsWith('zh') ? 'zh' : 'en'}
              onChange={(e) => setLanguage(e.target.value as 'en' | 'zh')}
            >
              <option value="en">{t('lang.english')}</option>
              <option value="zh">{t('lang.chinese')}</option>
            </select>
          </label>
          <button
            type="button"
            onClick={handleLogout}
            disabled={logout.isPending}
            className="px-3 py-1.5 rounded-md text-sm font-medium bg-slate-800 text-white hover:bg-slate-900 disabled:opacity-50"
          >
            {t('nav.logout')}
          </button>
        </div>
      </header>
      <main className="flex-1 p-6 max-w-6xl w-full mx-auto">{children}</main>
    </div>
  )
}
