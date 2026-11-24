import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthContext'

// Guards /dev/form-options itself, not just its nav link — gated on role
// (owner/admin), not job_function, matching the backend's
// require_job_function() with no arguments (api.py's /form-options
// mutation endpoints).
export function RequireAdminAccess() {
  const { user } = useAuth()
  if (user && user.role !== 'owner' && user.role !== 'admin') return <Navigate to="/dev" replace />
  return <Outlet />
}
