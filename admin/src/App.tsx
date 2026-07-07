import { Navigate, Route, Routes } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Login } from './pages/Login'
import { Jobs } from './pages/Jobs'
import { JobDetail } from './pages/JobDetail'
import { Users } from './pages/Users'
import { Expenses } from './pages/Expenses'
import { ExpenseDetail } from './pages/ExpenseDetail'
import { ReviewQueue } from './pages/ReviewQueue'
import { Suppliers } from './pages/Suppliers'
import { Capture } from './pages/Capture'
import { MyExpenses } from './pages/MyExpenses'
import { RequireAuth } from './components/RequireAuth'
import { RequireAdmin } from './components/RequireAdmin'
import { useMe } from './api/hooks/useAuth'

function Index() {
  const me = useMe()
  const { t } = useTranslation()
  if (me.isLoading) {
    return <p className="p-6 text-slate-500">{t('common.loading')}</p>
  }
  // R25/M1: a transient /me failure must not sit on a permanent "Loading".
  // Offer a retry (the outer RequireAuth already handles the no-token
  // redirect, so a token is present here).
  if (me.isError) {
    return (
      <div className="p-6">
        <p className="text-slate-500">{t('common.error')}</p>
        <button
          type="button"
          onClick={() => void me.refetch()}
          className="mt-3 px-4 py-2 rounded-md text-sm font-medium bg-slate-900 text-white hover:bg-slate-800"
        >
          {t('common.retry')}
        </button>
      </div>
    )
  }
  if (!me.data) {
    return <p className="p-6 text-slate-500">{t('common.loading')}</p>
  }
  return me.data.role === 'admin' ? (
    <Navigate to="/expenses" replace />
  ) : (
    <Navigate to="/capture" replace />
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Index />
          </RequireAuth>
        }
      />
      <Route
        path="/capture"
        element={
          <RequireAuth>
            <Capture />
          </RequireAuth>
        }
      />
      <Route
        path="/my-expenses"
        element={
          <RequireAuth>
            <MyExpenses />
          </RequireAuth>
        }
      />
      <Route
        path="/jobs"
        element={
          <RequireAuth>
            <RequireAdmin>
              <Jobs />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route
        path="/jobs/:id"
        element={
          <RequireAuth>
            <RequireAdmin>
              <JobDetail />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route
        path="/users"
        element={
          <RequireAuth>
            <RequireAdmin>
              <Users />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route
        path="/expenses"
        element={
          <RequireAuth>
            <RequireAdmin>
              <Expenses />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      {/* Expense detail is accessible to both roles. Backend RBAC
          enforces the actual ownership rules: admins see everything,
          contributors see only their own (and may edit own+pending).
          Admin-only UI controls (Delete, Audit log tab) are gated
          inside ExpenseDetail itself via useMe(). */}
      <Route
        path="/expenses/:id"
        element={
          <RequireAuth>
            <ExpenseDetail />
          </RequireAuth>
        }
      />
      <Route
        path="/review-queue"
        element={
          <RequireAuth>
            <RequireAdmin>
              <ReviewQueue />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route
        path="/suppliers"
        element={
          <RequireAuth>
            <RequireAdmin>
              <Suppliers />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
