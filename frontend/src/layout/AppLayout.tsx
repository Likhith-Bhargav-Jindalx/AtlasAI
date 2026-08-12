import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useRole, type ViewRole } from './RoleContext'

const NAV: Record<ViewRole, { to: string; label: string }[]> = {
  agent: [
    { to: '/agent', label: 'Request inbox' },
  ],
  dev: [
    { to: '/dev', label: 'Recording library' },
    { to: '/approvals', label: 'Heal approvals' },
  ],
  approver: [
    { to: '/approvals', label: 'Heal approvals' },
    { to: '/dev', label: 'Script registry' },
  ],
}

const ROLE_LABELS: Record<ViewRole, string> = {
  agent: 'Operations agent',
  dev: 'Developer',
  approver: 'Approver',
}

export function AppLayout() {
  const { user, logout } = useAuth()
  const { role, setRole } = useRole()

  return (
    <div className="flex min-h-screen bg-atlas-bg text-atlas-ink font-sans">
      <div className="w-[252px] flex-none bg-atlas-card border-r border-atlas-border flex flex-col p-3.5 gap-6 sticky top-0 h-screen box-border">
        <div className="flex items-center gap-2.5 px-2">
          <div className="w-7 h-7 rounded-[9px] bg-atlas-primary text-white flex items-center justify-center text-sm font-bold tracking-tight">A</div>
          <div className="flex flex-col">
            <div className="text-sm font-semibold tracking-tight">Atlas AI</div>
            <div className="text-[11px] text-atlas-body">{user?.email}</div>
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <div className="text-[10px] tracking-wider uppercase text-atlas-muted px-2 pb-1.5">{ROLE_LABELS[role]}</div>
          {NAV[role].map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-2 rounded-[10px] text-[13.5px] hover:bg-atlas-accentSubtle ${
                  isActive ? 'font-semibold' : 'text-atlas-body'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>

        <div className="mt-auto flex flex-col gap-2">
          <div className="text-[10px] tracking-wider uppercase text-atlas-muted px-2">Viewing as</div>
          <div className="flex flex-col gap-0.5 bg-[#f7f8f7] border border-atlas-border rounded-xl p-1">
            {(Object.keys(ROLE_LABELS) as ViewRole[]).map((r) => (
              <button
                key={r}
                onClick={() => setRole(r)}
                className={`px-2.5 py-1.5 rounded-[9px] text-[12.5px] text-left ${
                  r === role ? 'bg-white font-semibold' : 'text-atlas-body'
                }`}
              >
                {ROLE_LABELS[r]}
              </button>
            ))}
          </div>
          <button onClick={() => logout()} className="text-left text-[12.5px] text-atlas-body px-2 pt-2 hover:text-atlas-ink">
            Sign out
          </button>
        </div>
      </div>

      <div className="flex-1 min-w-0 flex flex-col">
        <Outlet />
      </div>
    </div>
  )
}
