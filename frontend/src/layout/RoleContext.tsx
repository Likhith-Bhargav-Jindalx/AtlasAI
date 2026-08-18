import { createContext, useContext, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

export type ViewRole = 'agent' | 'dev' | 'approver'

function defaultRoleFor(jobFunction: string | null | undefined): ViewRole {
  if (jobFunction === 'developer') return 'dev'
  return 'agent'
}

// An operations agent's view is locked to 'agent' — they can never switch
// to Developer or Approver, in the sidebar (AppLayout hides the other rows
// when `locked`) or by navigating to a /dev or /approvals route directly
// (see RequireDeveloperAccess, which checks the real job_function, not
// this switchable role).
function isRoleLocked(jobFunction: string | null | undefined): boolean {
  return jobFunction === 'operations_agent'
}

const RoleContext = createContext<{ role: ViewRole; setRole: (r: ViewRole) => void; locked: boolean } | null>(null)

export function RoleProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const locked = isRoleLocked(user?.job_function)
  const [role, setRoleState] = useState<ViewRole>(() => defaultRoleFor(user?.job_function))

  function setRole(r: ViewRole) {
    if (locked) return
    setRoleState(r)
  }

  return <RoleContext.Provider value={{ role, setRole, locked }}>{children}</RoleContext.Provider>
}

export function useRole() {
  const ctx = useContext(RoleContext)
  if (!ctx) throw new Error('useRole must be used within RoleProvider')
  return ctx
}
