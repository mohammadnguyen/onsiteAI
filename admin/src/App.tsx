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
  if (me.isLoading || !me.data) {
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
