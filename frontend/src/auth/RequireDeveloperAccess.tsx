import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthContext'

// Guards the /dev/* and /approvals routes themselves, not just the sidebar
// links — an operations agent typing one of those URLs directly must be
// redirected, not just fail to see a link to it. Checks the user's real
// job_function, not the switchable "viewing as" role, so a developer who
// happens to be viewing as agent can still navigate here directly.
export function RequireDeveloperAccess() {
  const { user } = useAuth()
  if (user?.job_function === 'operations_agent') return <Navigate to="/agent" replace />
  return <Outlet />
}
