import { createContext, useContext, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

export type ViewRole = 'agent' | 'dev' | 'approver'

function defaultRoleFor(jobFunction: string | null | undefined): ViewRole {
  if (jobFunction === 'developer') return 'dev'
  return 'agent'
}

const RoleContext = createContext<{ role: ViewRole; setRole: (r: ViewRole) => void } | null>(null)

export function RoleProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [role, setRole] = useState<ViewRole>(() => defaultRoleFor(user?.job_function))
  return <RoleContext.Provider value={{ role, setRole }}>{children}</RoleContext.Provider>
}

export function useRole() {
  const ctx = useContext(RoleContext)
  if (!ctx) throw new Error('useRole must be used within RoleProvider')
  return ctx
}
