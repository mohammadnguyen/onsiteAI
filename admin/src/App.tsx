import { Navigate, Route, Routes } from 'react-router-dom'
import { Login } from './pages/Login'
import { Jobs } from './pages/Jobs'
import { JobDetail } from './pages/JobDetail'
import { Users } from './pages/Users'
import { Expenses } from './pages/Expenses'
import { ExpenseDetail } from './pages/ExpenseDetail'
import { ReviewQueue } from './pages/ReviewQueue'
import { Suppliers } from './pages/Suppliers'
import { RequireAuth } from './components/RequireAuth'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/jobs"
        element={
          <RequireAuth>
            <Jobs />
          </RequireAuth>
        }
      />
      <Route
        path="/jobs/:id"
        element={
          <RequireAuth>
            <JobDetail />
          </RequireAuth>
        }
      />
      <Route
        path="/users"
        element={
          <RequireAuth>
            <Users />
          </RequireAuth>
        }
      />
      <Route
        path="/expenses"
        element={
          <RequireAuth>
            <Expenses />
          </RequireAuth>
        }
      />
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
            <ReviewQueue />
          </RequireAuth>
        }
      />
      <Route
        path="/suppliers"
        element={
          <RequireAuth>
            <Suppliers />
          </RequireAuth>
        }
      />
      <Route path="/" element={<Navigate to="/jobs" replace />} />
      <Route path="*" element={<Navigate to="/jobs" replace />} />
    </Routes>
  )
}
