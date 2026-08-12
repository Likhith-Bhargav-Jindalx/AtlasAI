import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function ProtectedRoute() {
  const { user, isLoading } = useAuth()

  if (isLoading) return <div className="p-8 text-atlas-body text-sm">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <Outlet />
}
