# Atlas AI Console Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the real React/Vite/TypeScript/Tailwind frontend for Atlas AI, implementing the six screens specified in `docs/design/atlas-ai-console-mockup.dc.html` (the approved visual design), wired to the actual backend — not mock data. An operations agent can request and record a flow and watch it back as a real video; a developer can browse recordings, generate a golden script from one, and review it with a synced video/step player; anyone can review and approve/reject a pending heal.

**Architecture:** A single-page app in a new top-level `frontend/` directory. TanStack Query owns all server state (recordings, requests, heal events, auth); React Router owns navigation; a small client-side "viewing as" role switcher (matching the mockup) lets anyone logged in preview all three surfaces, since per-role route/action authorization is explicitly deferred (per `2026-08-12-frontend-foundation-design.md`). One shared `VideoStepPlayer` component (video element + chapter timeline + step list, driven by `GET /recordings/{id}`'s `steps[].offset_ms`) is reused by both the agent's review screen and the developer's authoring workspace — the two places the mockup shows a video-plus-steps view.

**Tech Stack:** Vite, React 18, TypeScript (strict), Tailwind CSS v3, TanStack Query v5, React Router v6, lucide-react (icons), `@fontsource/general-sans` + `@fontsource/inter` (self-hosted fonts — substituted for the mockup's commercial Halenoir/HalenoirCompactText per the font-licensing decision earlier in this project), Vitest + Testing Library for the handful of tests that carry real logic (playhead-to-chapter mapping, auth redirect).

## Global Constraints

- Every screen renders real data from the backend built in the prior two plans (`recording-video-capture`, `frontend-backend-gaps`) — no mock/fixture data in the shipped app.
- Don't fabricate UI for data the backend doesn't have. The mockup's "editable title/description" on the review screen and the "Submit to developer" button have no backing fields/action (`persist_recording` already marks a linked request `submitted` the moment capture stops — there's nothing left to manually submit, and `Recording` has no title/description columns). These are intentionally adapted, not literally copied — see Task 8.
- The API base URL is configurable via `VITE_API_BASE_URL`, defaulting to `http://localhost:8000` (the FastAPI dev server's default port). The frontend dev server runs on Vite's default `5173`, matching the CORS origin already configured on the backend.
- `credentials: 'include'` on every API request — the session is an httpOnly cookie, not a bearer token.
- Follow the mockup's exact color values and layout structure (`docs/design/atlas-ai-console-mockup.dc.html`, `docs/design/anka-colors-and-type.css`) — Tailwind arbitrary-value syntax (e.g. `text-[13.5px]`, `rounded-[16px]`) is expected and encouraged where the mockup uses a value off Tailwind's default scale; don't round values to the nearest default token.
- After every task that changes what renders, run both dev servers (`uvicorn` for the backend, `npm run dev` for the frontend) and click through the affected screen in a browser before marking the task done — type-checking and unit tests verify code correctness, not visual/UX correctness.

---

### Task 1: Scaffold the Vite/React/TypeScript/Tailwind project

**Files:**
- Create: `frontend/` (full Vite scaffold), `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/src/index.css`, `frontend/src/main.tsx` (Vite default, to be replaced in Task 2)
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Scaffold with Vite**

```bash
cd /Users/apple/AtlasAI
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

- [ ] **Step 2: Install dependencies**

```bash
npm install react-router-dom @tanstack/react-query lucide-react
npm install @fontsource/inter @fontsource-variable/general-sans
npm install -D tailwindcss@^3 postcss autoprefixer
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @vitest/ui
npx tailwindcss init -p
```

- [ ] **Step 3: Configure Tailwind with the mockup's exact palette**

Replace `frontend/tailwind.config.ts` (rename the generated `tailwind.config.js` to `.ts` or keep `.js` — either works with Vite; use `.ts` for consistency with the rest of the project):

```typescript
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"General Sans Variable"', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        atlas: {
          primary: '#144e48',
          primaryHover: '#0e3a35',
          accent: '#b1d1c8',
          accentSubtle: '#edf5f3',
          ink: '#0d0d0c',
          body: '#78786f',
          muted: '#a3a39b',
          border: '#ebebea',
          borderHover: '#d8dad8',
          bg: '#f4f5f4',
          card: '#ffffff',
          rowBorder: '#f1f2f1',
          danger: '#ff5b5b',
          dangerHover: '#e64d4d',
          warnBg: '#fdf6e7',
          warnBorder: '#f0e2c0',
          warnIcon: '#8a6a1f',
          warnTextStrong: '#5c4712',
          warnText: '#7a6528',
          diffOldBg: '#fdf0f0',
          diffOldText: '#8a3a3a',
          diffNewBg: '#edf5f3',
          diffNewText: '#144e48',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
```

- [ ] **Step 4: Global styles and fonts**

Replace `frontend/src/index.css`:

```css
@import '@fontsource/inter/400.css';
@import '@fontsource/inter/500.css';
@import '@fontsource/inter/600.css';
@import '@fontsource-variable/general-sans';

@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  margin: 0;
  background: #f4f5f4;
  color: #0d0d0c;
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **Step 5: Configure Vitest**

Add to `frontend/vite.config.ts` (merge with the generated config's `defineConfig({...})`):

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    globals: true,
  },
})
```

Create `frontend/src/test-setup.ts`:

```typescript
import '@testing-library/jest-dom/vitest'
```

Add to `frontend/package.json`'s `"scripts"`:

```json
"test": "vitest run"
```

- [ ] **Step 6: Write a smoke test and verify the toolchain**

```typescript
// frontend/src/App.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders without crashing', () => {
    render(<App />)
    expect(document.body).toBeTruthy()
  })
})
```

Run: `npm run test`
Expected: PASS (the default Vite `App.tsx` renders fine as-is at this point — it'll be replaced in Task 2, at which point this test will need `BrowserRouter`/`QueryClientProvider` wrapping, handled there).

Run: `npm run build`
Expected: builds cleanly with no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/apple/AtlasAI
git add frontend/
git commit -m "chore: scaffold Vite/React/TypeScript/Tailwind frontend"
```

---

### Task 2: API client, auth, and the login screen

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/auth/AuthContext.tsx`, `frontend/src/auth/ProtectedRoute.tsx`, `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `apiFetch<T>(path: string, init?: RequestInit): Promise<T>` (throws `ApiError` with `.status` on non-2xx), `useAuth()` returning `{ user, isLoading, login, logout }`, `<ProtectedRoute>` wrapping any route needing a session.

- [ ] **Step 1: API client**

```typescript
// frontend/src/lib/api.ts
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, body.detail ?? response.statusText)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface MeResponse {
  user_id: string
  org_id: string
  email: string
  role: string
  job_function: string | null
}

export function login(email: string, password: string) {
  return apiFetch<MeResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
}

export function logout() {
  return apiFetch<{ ok: boolean }>('/auth/logout', { method: 'POST' })
}

export function fetchMe() {
  return apiFetch<MeResponse>('/auth/me')
}
```

- [ ] **Step 2: Auth context**

```tsx
// frontend/src/auth/AuthContext.tsx
import { createContext, useContext, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, fetchMe, login as apiLogin, logout as apiLogout, type MeResponse } from '../lib/api'

interface AuthValue {
  user: MeResponse | null | undefined
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    retry: false,
    // A 401 is an expected "not logged in" state, not a transient error —
    // surface it as `data: null` instead of leaving the query stuck in
    // `error` state forever.
    throwOnError: false,
  })

  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => apiLogin(email, password),
    onSuccess: (user) => queryClient.setQueryData(['me'], user),
  })

  const logoutMutation = useMutation({
    mutationFn: apiLogout,
    onSuccess: () => queryClient.setQueryData(['me'], null),
  })

  const user = meQuery.isError && meQuery.error instanceof ApiError && meQuery.error.status === 401
    ? null
    : meQuery.data

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading: meQuery.isLoading,
        login: async (email, password) => { await loginMutation.mutateAsync({ email, password }) },
        logout: async () => { await logoutMutation.mutateAsync() },
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
```

- [ ] **Step 3: Protected route**

```tsx
// frontend/src/auth/ProtectedRoute.tsx
import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function ProtectedRoute() {
  const { user, isLoading } = useAuth()

  if (isLoading) return <div className="p-8 text-atlas-body text-sm">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <Outlet />
}
```

- [ ] **Step 4: Login page**

```tsx
// frontend/src/pages/LoginPage.tsx
import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../lib/api'

export function LoginPage() {
  const { user, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to="/" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-atlas-bg">
      <form onSubmit={handleSubmit} className="w-full max-w-sm bg-atlas-card border border-atlas-border rounded-2xl p-8 flex flex-col gap-5">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-[9px] bg-atlas-primary text-white flex items-center justify-center text-sm font-bold">A</div>
          <div className="font-display text-lg font-semibold tracking-tight">Atlas AI</div>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-atlas-ink" htmlFor="email">Email</label>
          <input
            id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-[15px]"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-atlas-ink" htmlFor="password">Password</label>
          <input
            id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-[15px]"
          />
        </div>
        {error && <div className="text-sm text-atlas-diffOldText">{error}</div>}
        <button
          type="submit" disabled={submitting}
          className="mt-1 bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 5: Wire providers and a minimal route shell**

```tsx
// frontend/src/main.tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './auth/AuthContext'
import './index.css'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
```

```tsx
// frontend/src/App.tsx
import { Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { ProtectedRoute } from './auth/ProtectedRoute'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<div className="p-8">Signed in.</div>} />
      </Route>
    </Routes>
  )
}
```

- [ ] **Step 6: Update the smoke test for the new providers**

```tsx
// frontend/src/App.test.tsx
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'
import { AuthProvider } from './auth/AuthContext'

function renderApp() {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>,
  )
}

describe('App', () => {
  it('redirects to /login when signed out', async () => {
    renderApp()
    expect(await screen.findByText(/sign in/i)).toBeInTheDocument()
  })
})
```

Run: `npm run test`
Expected: PASS (the `/auth/me` call fails against no running backend in the test environment — `fetch` rejects, which the test doesn't stub; note this in Step 7 below).

- [ ] **Step 7: Handle the no-backend-in-tests case**

The test above only works if `fetchMe`'s rejection is treated as "not logged in" rather than an uncaught rejection. Confirm `AuthProvider`'s `meQuery` config (`throwOnError: false`, `retry: false`) is enough for TanStack Query to settle into an error state without crashing the render — if the test fails with an unhandled rejection instead of finding the login text, add a `queryFn` wrapper in the test file that mocks `global.fetch` to reject immediately, so the test doesn't depend on network timing:

```tsx
// add near the top of frontend/src/App.test.tsx if Step 6 didn't pass cleanly
import { beforeEach, vi } from 'vitest'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('no backend in tests')))
})
```

Run: `npm run test`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib frontend/src/auth frontend/src/pages/LoginPage.tsx frontend/src/main.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat: add API client, auth context, and login screen"
```

---

### Task 3: App layout — sidebar, role switcher, and routing shell

**Files:**
- Create: `frontend/src/layout/AppLayout.tsx`, `frontend/src/layout/RoleContext.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `useRole()` returning `{ role: 'agent' | 'dev' | 'approver', setRole }`, defaulting from the signed-in user's `job_function` (`operations_agent` → `agent`, `developer` → `dev`, anything else → `agent`) but freely switchable — this is the mockup's "Viewing as" affordance, a deliberate stand-in for real per-role route guards (deferred, per the frontend design spec's Non-Goals).

- [ ] **Step 1: Role context**

```tsx
// frontend/src/layout/RoleContext.tsx
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
```

- [ ] **Step 2: App layout with sidebar**

```tsx
// frontend/src/layout/AppLayout.tsx
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useRole, type ViewRole } from './RoleContext'

const NAV: Record<ViewRole, { to: string; label: string }[]> = {
  agent: [
    { to: '/agent', label: 'Request inbox' },
    { to: '/agent/recordings', label: 'My submissions' },
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
```

- [ ] **Step 3: Wire the layout into routing**

```tsx
// frontend/src/App.tsx
import { Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './layout/AppLayout'
import { RoleProvider } from './layout/RoleContext'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route
          element={
            <RoleProvider>
              <AppLayout />
            </RoleProvider>
          }
        >
          <Route path="/" element={<div className="p-8">Pick a screen from the sidebar.</div>} />
          <Route path="/agent" element={<div className="p-8">Inbox — Task 5</div>} />
          <Route path="/dev" element={<div className="p-8">Library — Task 9</div>} />
          <Route path="/approvals" element={<div className="p-8">Approvals — Task 11</div>} />
        </Route>
      </Route>
    </Routes>
  )
}
```

- [ ] **Step 4: Manual check**

Run `npm run dev` (frontend) and `uvicorn atlas_engine.api:app --reload` (backend, from `engine/`), log in with a user created via `atlas create-user`, and confirm: the sidebar renders, nav items change when clicking each "Viewing as" row, and each stub route renders its placeholder text.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/layout frontend/src/App.tsx
git commit -m "feat: add app layout with sidebar and viewing-as role switcher"
```

---

### Task 4: Shared UI primitives — StatusPill and formatting helpers

**Files:**
- Create: `frontend/src/components/StatusPill.tsx`, `frontend/src/lib/format.ts`
- Test: `frontend/src/lib/format.test.ts`

**Interfaces:**
- Produces: `<StatusPill status="certified" />` (colors itself from a fixed status→color map covering every status string used across `recordings`, `recording_requests`, `golden_scripts`, and `heal_events`), `formatDuration(seconds: number | null): string` (`"3:12"`, `"—"` for null), `formatRelativeTime(iso: string): string` (`"2 hours ago"`).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/lib/format.test.ts
import { describe, expect, it } from 'vitest'
import { formatDuration } from './format'

describe('formatDuration', () => {
  it('formats seconds as m:ss', () => {
    expect(formatDuration(192)).toBe('3:12')
    expect(formatDuration(5)).toBe('0:05')
  })

  it('renders a dash for null', () => {
    expect(formatDuration(null)).toBe('—')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test`
Expected: FAIL — `format.ts` doesn't exist.

- [ ] **Step 3: Implement**

```typescript
// frontend/src/lib/format.ts
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${remainder.toString().padStart(2, '0')}`
}

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  const diffSeconds = Math.floor((Date.now() - then) / 1000)
  if (diffSeconds < 60) return 'just now'
  const diffMinutes = Math.floor(diffSeconds / 60)
  if (diffMinutes < 60) return `${diffMinutes} minute${diffMinutes === 1 ? '' : 's'} ago`
  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`
}
```

```tsx
// frontend/src/components/StatusPill.tsx
const PILL_COLORS: Record<string, { bg: string; fg: string }> = {
  draft: { bg: '#f4f5f4', fg: '#78786f' },
  captured: { bg: '#edf5f3', fg: '#144e48' },
  failed: { bg: '#fdf0f0', fg: '#8a3a3a' },
  discarded: { bg: '#f4f5f4', fg: '#78786f' },
  open: { bg: '#fdf6e7', fg: '#8a6a1f' },
  submitted: { bg: '#edf5f3', fg: '#144e48' },
  fulfilled: { bg: '#144e48', fg: '#ffffff' },
  cancelled: { bg: '#f4f5f4', fg: '#78786f' },
  certified: { bg: '#144e48', fg: '#ffffff' },
  active: { bg: '#144e48', fg: '#ffffff' },
  deprecated: { bg: '#f4f5f4', fg: '#78786f' },
  pending_approval: { bg: '#fdf6e7', fg: '#8a6a1f' },
  approved: { bg: '#edf5f3', fg: '#144e48' },
  rejected: { bg: '#fdf0f0', fg: '#8a3a3a' },
  auto_approved: { bg: '#edf5f3', fg: '#144e48' },
}

export function StatusPill({ status }: { status: string }) {
  const colors = PILL_COLORS[status] ?? PILL_COLORS.draft
  const label = status.replace(/_/g, ' ')
  return (
    <span
      className="inline-flex items-center text-[11.5px] px-[11px] py-1 rounded-full capitalize"
      style={{ background: colors.bg, color: colors.fg }}
    >
      {label}
    </span>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StatusPill.tsx frontend/src/lib/format.ts frontend/src/lib/format.test.ts
git commit -m "feat: add StatusPill and duration/time formatting helpers"
```

---

### Task 5: Agent — Request inbox

**Files:**
- Create: `frontend/src/lib/recordingRequests.ts`, `frontend/src/pages/agent/InboxPage.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `useRecordingRequests(orgId, status?)`, `useCreateRecordingRequest()` (TanStack Query hooks over `GET/POST /recording-requests`).

- [ ] **Step 1: Recording-requests API + hooks**

```typescript
// frontend/src/lib/recordingRequests.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingRequestSummary {
  id: string
  title: string
  target_url: string
  status: string
  claimed_by: string | null
}

export function useRecordingRequests(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['recording-requests', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<RecordingRequestSummary[]>(`/recording-requests?${params}`)
    },
  })
}

export function useCreateRecordingRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { org_id: string; requested_by: string; target_url: string; title: string }) =>
      apiFetch<{ request_id: string }>('/recording-requests', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording-requests'] }),
  })
}
```

- [ ] **Step 2: Inbox page**

```tsx
// frontend/src/pages/agent/InboxPage.tsx
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useCreateRecordingRequest, useRecordingRequests } from '../../lib/recordingRequests'
import { StatusPill } from '../../components/StatusPill'

export function InboxPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const orgId = user!.org_id
  const { data: openRequests } = useRecordingRequests(orgId, 'open')
  const { data: submittedRequests } = useRecordingRequests(orgId, 'submitted')
  const createRequest = useCreateRecordingRequest()

  const [showForm, setShowForm] = useState(false)
  const [title, setTitle] = useState('')
  const [targetUrl, setTargetUrl] = useState('')

  const requests = [...(openRequests ?? []), ...(submittedRequests ?? [])]

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    const result = await createRequest.mutateAsync({
      org_id: orgId, requested_by: user!.user_id, target_url: targetUrl, title,
    })
    navigate(`/agent/record?requestId=${result.request_id}&url=${encodeURIComponent(targetUrl)}`)
  }

  function startRecording(request: { id: string; target_url: string }) {
    navigate(`/agent/record?requestId=${request.id}&url=${encodeURIComponent(request.target_url)}`)
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex items-end justify-between gap-6">
        <div className="flex flex-col gap-1.5">
          <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Request inbox</h1>
          <div className="text-sm text-atlas-body">Open recording requests. Record the flow once — we handle the rest.</div>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 bg-atlas-primary hover:bg-atlas-primaryHover text-white px-[18px] py-2.5 rounded-full text-[13.5px] font-medium whitespace-nowrap"
        >
          <span className="w-2 h-2 rounded-full bg-atlas-danger" />
          Start new recording
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-3 max-w-md">
          <input
            required placeholder="Title (e.g. Check eligibility)" value={title} onChange={(e) => setTitle(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm"
          />
          <input
            required type="url" placeholder="https://portal.example.com/..." value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm"
          />
          <button type="submit" className="bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium">
            Create and start recording
          </button>
        </form>
      )}

      {requests.length > 0 ? (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
          <div className="grid grid-cols-[1.7fr_1.2fr_.7fr_auto] gap-5 px-[22px] py-3 border-b border-atlas-border bg-[#fafbfa] text-[11px] tracking-wider uppercase text-atlas-muted">
            <div>Task</div><div>Target portal</div><div>Status</div><div className="w-[104px]" />
          </div>
          {requests.map((r) => (
            <div key={r.id} className="grid grid-cols-[1.7fr_1.2fr_.7fr_auto] gap-5 px-[22px] py-[18px] border-b border-atlas-rowBorder items-center hover:bg-[#fbfcfb]">
              <div className="text-sm font-medium tracking-tight">{r.title}</div>
              <div className="text-[12.5px] text-atlas-body truncate">{r.target_url}</div>
              <div><StatusPill status={r.status} /></div>
              <button
                onClick={() => startRecording(r)}
                className="w-[104px] text-center text-[12.5px] py-2 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle"
              >
                Record
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <Inbox size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">No open requests</div>
          <div className="text-[13.5px] text-atlas-body max-w-[380px]">
            Record a flow on your own initiative with the button above, or wait for one to land here.
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire the route**

In `frontend/src/App.tsx`, replace the `/agent` placeholder route with `<Route path="/agent" element={<InboxPage />} />` and import `InboxPage` from `./pages/agent/InboxPage`.

- [ ] **Step 4: Manual check**

With both dev servers running, log in, switch "Viewing as" to Operations agent, confirm the inbox loads (empty state if no requests exist yet), create a request via the form, confirm it appears and "Record" navigates to `/agent/record?...` (a 404/blank page is expected until Task 6).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/recordingRequests.ts frontend/src/pages/agent/InboxPage.tsx frontend/src/App.tsx
git commit -m "feat: add agent request inbox screen"
```

---

### Task 6: Agent — Live recording

**Files:**
- Create: `frontend/src/lib/recordings.ts`, `frontend/src/pages/agent/LiveRecordingPage.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `useStartRecording()`, `useStopRecording()` (mutations over `POST /recordings/start` / `POST /recordings/{id}/stop`).

- [ ] **Step 1: Recordings API module (start of a file Tasks 7–10 extend)**

```typescript
// frontend/src/lib/recordings.ts
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingSummary {
  id: string
  status: string
  role: string
  request_id: string | null
  request_title: string | null
  portal_domain: string | null
  duration_seconds: number | null
  has_video: boolean
  created_at: string
}

export interface RecordingStep {
  order_index: number
  caption: string | null
  offset_ms: number | null
}

export interface RecordingDetail {
  id: string
  status: string
  role: string
  duration_seconds: number | null
  has_video: boolean
  video_asset_id: string | null
  steps: RecordingStep[]
}

export function useRecordings(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['recordings', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<RecordingSummary[]>(`/recordings?${params}`)
    },
  })
}

export function useRecordingDetail(recordingId: string | undefined) {
  return useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => apiFetch<RecordingDetail>(`/recordings/${recordingId}`),
    enabled: !!recordingId,
  })
}

export function useStartRecording() {
  return useMutation({
    mutationFn: (body: { org_id: string; start_url: string; request_id?: string; role?: string }) =>
      apiFetch<{ capture_session_id: string }>('/recordings/start', { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ recording_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}

export function recordingVideoUrl(recordingId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/recordings/${recordingId}/video`
}
```

- [ ] **Step 2: Live recording page**

```tsx
// frontend/src/pages/agent/LiveRecordingPage.tsx
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MonitorPlay, Square } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useStartRecording, useStopRecording } from '../../lib/recordings'

export function LiveRecordingPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const startUrl = searchParams.get('url') ?? ''
  const requestId = searchParams.get('requestId') ?? undefined

  const startRecording = useStartRecording()
  const stopRecording = useStopRecording()
  const [captureSessionId, setCaptureSessionId] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const startedRef = useRef(false)

  useEffect(() => {
    if (startedRef.current || !startUrl) return
    startedRef.current = true
    startRecording
      .mutateAsync({ org_id: user!.org_id, start_url: startUrl, request_id: requestId, role: 'demo' })
      .then((result) => setCaptureSessionId(result.capture_session_id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startUrl])

  useEffect(() => {
    if (!captureSessionId) return
    const interval = setInterval(() => setElapsedSeconds((s) => s + 1), 1000)
    return () => clearInterval(interval)
  }, [captureSessionId])

  async function handleStop() {
    if (!captureSessionId) return
    const result = await stopRecording.mutateAsync(captureSessionId)
    navigate(`/agent/recordings/${result.recording_id}`)
  }

  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60
  const elapsed = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`

  return (
    <div className="min-h-screen bg-[#0d0d0c] flex flex-col items-center px-10 pt-8 pb-10 gap-6 box-border">
      <div className="w-full max-w-[1080px] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-atlas-danger animate-pulse" />
          <span className="text-white text-sm font-medium tracking-tight">Recording</span>
          <span className="text-atlas-body text-[13px]">{startUrl}</span>
        </div>
        <div className="text-white text-[22px] tabular-nums tracking-wide">{elapsed}</div>
      </div>

      <div className="w-full max-w-[1080px] aspect-video rounded-[18px] overflow-hidden border border-[#2a2a28] bg-[#141413] flex flex-col items-center justify-center gap-3.5 text-[#5c5c56]">
        <MonitorPlay size={34} />
        <div className="text-[13px]">Live capture in progress in a separate browser window</div>
        <div className="text-xs text-[#3f3f3b]">Switch to it, perform the flow, then come back and stop here.</div>
      </div>

      <button
        onClick={handleStop}
        disabled={!captureSessionId || stopRecording.isPending}
        className="flex items-center gap-2.5 bg-atlas-danger hover:bg-atlas-dangerHover text-white px-[26px] py-3.5 rounded-full text-sm font-semibold disabled:opacity-50"
      >
        <Square size={11} fill="white" />
        {stopRecording.isPending ? 'Stopping…' : 'Stop and review'}
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Wire the route**

Add `<Route path="/agent/record" element={<LiveRecordingPage />} />` to `App.tsx`.

- [ ] **Step 4: Manual check**

From the inbox, click "Record" — confirm a real headed Chromium window opens (the backend's live-capture browser), the elapsed timer counts up, and clicking "Stop and review" closes it and navigates to `/agent/recordings/:id` (blank/404 until Task 8).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/recordings.ts frontend/src/pages/agent/LiveRecordingPage.tsx frontend/src/App.tsx
git commit -m "feat: add live recording screen wired to start/stop endpoints"
```

---

### Task 7: Shared `VideoStepPlayer` component

**Files:**
- Create: `frontend/src/components/VideoStepPlayer.tsx`, `frontend/src/components/VideoStepPlayer.test.tsx`

**Interfaces:**
- Produces: `<VideoStepPlayer recordingId={id} hasVideo={bool} steps={RecordingStep[]} durationSeconds={number | null} />`. Internally computes the active step from the video's `timeupdate` currentTime vs. each step's `offset_ms`; clicking a step seeks the video.

- [ ] **Step 1: Write the failing test (pure logic, no DOM video needed)**

```tsx
// frontend/src/components/VideoStepPlayer.test.tsx
import { describe, expect, it } from 'vitest'
import { activeStepIndex } from './VideoStepPlayer'

describe('activeStepIndex', () => {
  const steps = [
    { order_index: 0, caption: 'a', offset_ms: 0 },
    { order_index: 1, caption: 'b', offset_ms: 5000 },
    { order_index: 2, caption: 'c', offset_ms: 12000 },
  ]

  it('picks the last step whose offset has passed', () => {
    expect(activeStepIndex(steps, 0)).toBe(0)
    expect(activeStepIndex(steps, 4.9)).toBe(0)
    expect(activeStepIndex(steps, 5)).toBe(1)
    expect(activeStepIndex(steps, 20)).toBe(2)
  })

  it('returns -1 for no steps', () => {
    expect(activeStepIndex([], 5)).toBe(-1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test`
Expected: FAIL — `VideoStepPlayer.tsx` doesn't exist.

- [ ] **Step 3: Implement**

```tsx
// frontend/src/components/VideoStepPlayer.tsx
import { useRef, useState } from 'react'
import { Play, VideoOff } from 'lucide-react'
import { recordingVideoUrl } from '../lib/recordings'
import { formatDuration } from '../lib/format'
import type { RecordingStep } from '../lib/recordings'

export function activeStepIndex(steps: RecordingStep[], currentTimeSeconds: number): number {
  let active = -1
  for (let i = 0; i < steps.length; i++) {
    const offsetSeconds = (steps[i].offset_ms ?? 0) / 1000
    if (offsetSeconds <= currentTimeSeconds) active = i
  }
  return active
}

interface Props {
  recordingId: string
  hasVideo: boolean
  steps: RecordingStep[]
  durationSeconds: number | null
}

export function VideoStepPlayer({ recordingId, hasVideo, steps, durationSeconds }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const active = activeStepIndex(steps, currentTime)

  function seekTo(offsetMs: number | null) {
    if (!videoRef.current || offsetMs === null) return
    videoRef.current.currentTime = offsetMs / 1000
    videoRef.current.play()
  }

  return (
    <div className="grid grid-cols-[1.65fr_1fr] gap-6 items-start">
      <div className="flex flex-col gap-3.5">
        {hasVideo ? (
          <video
            ref={videoRef}
            src={recordingVideoUrl(recordingId)}
            controls
            className="rounded-2xl overflow-hidden border border-atlas-border bg-black aspect-video w-full"
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          />
        ) : (
          <div className="rounded-2xl border border-atlas-border bg-atlas-bg aspect-video flex flex-col items-center justify-center gap-2 text-atlas-muted">
            <VideoOff size={28} />
            <div className="text-[13px]">No video available for this recording</div>
          </div>
        )}
      </div>

      <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
        <div className="px-[18px] py-3.5 border-b border-atlas-border flex items-center justify-between">
          <div className="text-[13.5px] font-semibold">Steps</div>
          <div className="text-xs text-atlas-muted">{formatDuration(durationSeconds)}</div>
        </div>
        <div className="max-h-[520px] overflow-auto">
          {steps.length === 0 && (
            <div className="p-[18px] text-[13px] text-atlas-body">No actions were recorded.</div>
          )}
          {steps.map((step, i) => (
            <button
              key={step.order_index}
              onClick={() => seekTo(step.offset_ms)}
              disabled={!hasVideo}
              className={`w-full text-left flex gap-3 px-[18px] py-3.5 border-b border-atlas-rowBorder hover:bg-[#fbfcfb] ${
                i === active ? 'bg-atlas-accentSubtle' : ''
              }`}
            >
              <div className="w-[22px] h-[22px] flex-none rounded-full bg-atlas-accentSubtle text-atlas-primary text-[11px] flex items-center justify-center tabular-nums">
                {step.order_index + 1}
              </div>
              <div className="flex flex-col gap-0.5 min-w-0">
                <div className="text-[13px]">{step.caption ?? 'Action'}</div>
                {step.offset_ms !== null && (
                  <div className="text-[11.5px] text-atlas-muted tabular-nums">
                    {formatDuration(Math.round(step.offset_ms / 1000))}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/VideoStepPlayer.tsx frontend/src/components/VideoStepPlayer.test.tsx
git commit -m "feat: add shared VideoStepPlayer component"
```

---

### Task 8: Agent — Review screen

**Files:**
- Create: `frontend/src/pages/agent/ReviewPage.tsx`
- Modify: `frontend/src/App.tsx`

The mockup's review screen has an editable title/description and a "Submit to developer" button. Neither has a backing field or action: `Recording` has no title/description columns, and `persist_recording` already marks a linked request `submitted` the instant capture stops (see `recording_service.py`) — there's nothing left to manually submit. This screen instead shows real identifying context (portal domain, role, action/step count, capture status) as read-only, and its one action is "Done" (back to the inbox). This is a deliberate adaptation, not a corner cut — see the plan's Global Constraints.

- [ ] **Step 1: Implement**

```tsx
// frontend/src/pages/agent/ReviewPage.tsx
import { useNavigate, useParams } from 'react-router-dom'
import { useRecordingDetail } from '../../lib/recordings'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function ReviewPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <StatusPill status={recording.status} />
            <span className="text-[12.5px] text-atlas-muted">
              {formatDuration(recording.duration_seconds)} · {recording.steps.length} action{recording.steps.length === 1 ? '' : 's'}
            </span>
          </div>
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Recording review</h1>
          <div className="text-[13.5px] text-atlas-body">
            {recording.role === 'demo' ? 'Demo recording' : 'Authoring recording'}
          </div>
        </div>
        <button
          onClick={() => navigate('/agent')}
          className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium"
        >
          Done
        </button>
      </div>

      <VideoStepPlayer
        recordingId={recording.id}
        hasVideo={recording.has_video}
        steps={recording.steps}
        durationSeconds={recording.duration_seconds}
      />
    </div>
  )
}
```

`GET /recordings/{id}` doesn't return `request_id` (only the list endpoint does), so the header copy above only distinguishes `demo`/`authoring` role — nothing about the linked request is shown here.

- [ ] **Step 2: Wire the route**

Add `<Route path="/agent/recordings/:recordingId" element={<ReviewPage />} />` to `App.tsx`.

- [ ] **Step 3: Manual check**

Run a full record → stop cycle from the inbox and confirm the review screen loads with a real playable video and a clickable step list that seeks the video.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/agent/ReviewPage.tsx frontend/src/App.tsx
git commit -m "feat: add agent recording review screen with real video playback"
```

---

### Task 9: Developer — Recording library

**Files:**
- Create: `frontend/src/pages/dev/LibraryPage.tsx`
- Modify: `frontend/src/App.tsx`

The mockup's library cards show a "Claim" action, but claiming (`claim_recording_request`) applies to a *request*, before a recording necessarily exists — not to an already-captured recording. This screen lists recordings (via `GET /recordings`) with an "Author" action per row, which is the real next step once a recording exists. A separate developer-side request queue (claim an open/submitted request) is deliberately out of scope for this pass — `claim_recording_request` already has full API support; wiring a dedicated screen for it is future work, not a gap in what's built.

- [ ] **Step 1: Implement**

```tsx
// frontend/src/pages/dev/LibraryPage.tsx
import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useRecordings } from '../../lib/recordings'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function LibraryPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data: recordings, isLoading } = useRecordings(user!.org_id)

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-[26px] max-w-[1280px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Recording library</h1>
        <div className="text-sm text-atlas-body">Recordings waiting to become golden scripts.</div>
      </div>

      {recordings && recordings.length > 0 ? (
        <div className="grid grid-cols-3 gap-5">
          {recordings.map((r) => (
            <button
              key={r.id}
              onClick={() => navigate(`/dev/recordings/${r.id}`)}
              className="text-left bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden hover:shadow-lg hover:-translate-y-0.5 transition-all"
            >
              <div className="aspect-video bg-[#e8eae8] relative flex items-center justify-center">
                {!r.has_video && <VideoOff size={24} className="text-atlas-muted" />}
                <div className="absolute right-2.5 bottom-2.5 text-[11px] text-white bg-black/55 px-2 py-0.5 rounded-full tabular-nums">
                  {formatDuration(r.duration_seconds)}
                </div>
              </div>
              <div className="p-4 flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2.5">
                  <div className="text-[14.5px] font-medium tracking-tight">{r.request_title ?? r.portal_domain ?? 'Recording'}</div>
                  <StatusPill status={r.status} />
                </div>
                <div className="text-[12.5px] text-atlas-body">{r.portal_domain}</div>
                <div className="text-[12px] text-atlas-muted pt-1.5 border-t border-atlas-rowBorder">
                  {formatRelativeTime(r.created_at)}
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <VideoOff size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">No recordings yet</div>
          <div className="text-[13.5px] text-atlas-body max-w-[400px]">
            Ask an operations agent to record a workflow, or record it yourself.
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Wire the route**

Replace the `/dev` placeholder in `App.tsx` with `<Route path="/dev" element={<LibraryPage />} />`.

- [ ] **Step 3: Manual check**

Switch "Viewing as" to Developer, confirm recordings made in earlier tasks show up as cards with correct status/duration/domain.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/dev/LibraryPage.tsx frontend/src/App.tsx
git commit -m "feat: add developer recording library screen"
```

---

### Task 10: Developer — Authoring workspace

**Files:**
- Create: `frontend/src/lib/authoring.ts`, `frontend/src/pages/dev/AuthoringPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Authoring API hook**

```typescript
// frontend/src/lib/authoring.ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface AuthorResult {
  status: string
  script_path: string
  golden_script_id: string
}

export function useAuthorRecording(recordingId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<AuthorResult>(`/recordings/${recordingId}/author`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording', recordingId] }),
  })
}
```

- [ ] **Step 2: Authoring page**

```tsx
// frontend/src/pages/dev/AuthoringPage.tsx
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRecordingDetail } from '../../lib/recordings'
import { useAuthorRecording } from '../../lib/authoring'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function AuthoringPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)
  const authorRecording = useAuthorRecording(recordingId!)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="flex flex-col h-screen box-border">
      <div className="flex-none px-7 py-[18px] border-b border-atlas-border bg-atlas-card flex items-center justify-between gap-5">
        <div className="flex items-center gap-3.5 min-w-0">
          <button
            onClick={() => navigate('/dev')}
            className="w-[30px] h-[30px] rounded-full border border-atlas-border flex items-center justify-center hover:bg-atlas-bg flex-none"
          >
            <ArrowLeft size={15} className="text-atlas-body" />
          </button>
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[15px] font-semibold tracking-tight">Authoring workspace</div>
            <div className="text-xs text-atlas-muted">{formatDuration(recording.duration_seconds)} · {recording.steps.length} actions</div>
          </div>
          {authorRecording.data && <StatusPill status={authorRecording.data.status} />}
        </div>
        <button
          onClick={() => authorRecording.mutate()}
          disabled={authorRecording.isPending}
          className="px-[18px] py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium disabled:opacity-60"
        >
          {authorRecording.isPending ? 'Generating…' : authorRecording.data ? 'Regenerate golden script' : 'Generate golden script'}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-7">
        <VideoStepPlayer
          recordingId={recording.id}
          hasVideo={recording.has_video}
          steps={recording.steps}
          durationSeconds={recording.duration_seconds}
        />
        {authorRecording.data && (
          <div className="mt-5 text-[13px] text-atlas-body">
            Golden script <span className="font-medium text-atlas-ink">{authorRecording.data.golden_script_id}</span> — {authorRecording.data.status}
          </div>
        )}
        {authorRecording.isError && (
          <div className="mt-5 text-[13px] text-atlas-diffOldText">{(authorRecording.error as Error).message}</div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wire the route**

Add `<Route path="/dev/recordings/:recordingId" element={<AuthoringPage />} />` to `App.tsx`.

- [ ] **Step 4: Manual check**

From the library, open a recording, click "Generate golden script", confirm the status pill updates and the golden script id appears (or, for a recording whose trajectory can't certify, confirm the error surfaces instead of failing silently).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/authoring.ts frontend/src/pages/dev/AuthoringPage.tsx frontend/src/App.tsx
git commit -m "feat: add developer authoring workspace with golden-script generation"
```

---

### Task 11: Heal approvals

**Files:**
- Modify: `engine/src/atlas_engine/verify/heal.py`, `engine/src/atlas_engine/api.py`, `engine/tests/test_api_heal_list.py` (backend: add `portal_domain` to the list so the UI can label each heal event by portal, not just a bare id)
- Create: `frontend/src/lib/healEvents.ts`, `frontend/src/pages/ApprovalsPage.tsx`
- Modify: `frontend/src/App.tsx`

The mockup's approval detail shows a narrative "why this drifted" explanation and a "current vs. proposed" frame comparison. Neither has real backing data (`heal_events.diff_summary` is a mechanical `[{step_index, field, old_value, new_value}, ...]` list, not prose, and there are no per-step frames/screenshots for a golden script's steps — only for a *recording's* video). The approvals screen renders the real diff array as an old→new selector list per changed step instead of inventing narrative text or frame images.

- [ ] **Step 1: Add `portal_domain` to `list_heal_events`**

In `engine/src/atlas_engine/verify/heal.py`, extend the existing `db.models` import to add `PortalProfile`, then update `list_heal_events`:

```python
from atlas_engine.db.models import GoldenScript, HealEvent, PortalProfile
```

```python
def list_heal_events(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(HealEvent).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)

        results = []
        for event in query.order_by(HealEvent.created_at.desc()).all():
            golden_script = session.get(GoldenScript, event.golden_script_id)
            profile = session.get(PortalProfile, golden_script.portal_profile_id) if golden_script else None
            results.append({
                "id": str(event.id),
                "golden_script_id": str(event.golden_script_id),
                "portal_domain": profile.domain if profile is not None else None,
                "trigger": event.trigger,
                "status": event.status,
                "diff_summary": event.diff_summary,
                "created_at": event.created_at.isoformat(),
            })
        return results
```

- [ ] **Step 2: Update the existing test's assertion and run it**

Add to `engine/tests/test_api_heal_list.py`'s existing test, after the current assertions:

```python
    assert body[0]["portal_domain"] is not None
```

Run: `cd engine && .venv/bin/pytest tests/test_api_heal_list.py -v`
Expected: PASS (the join was already correct; this just locks in the new field).

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 3: Commit the backend change**

```bash
git add engine/src/atlas_engine/verify/heal.py engine/tests/test_api_heal_list.py
git commit -m "feat: include portal_domain in heal event listings"
```

- [ ] **Step 4: Frontend heal-events API hook**

```typescript
// frontend/src/lib/healEvents.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface HealDiffEntry {
  step_index: number
  field: string
  old_value: string
  new_value: string
}

export interface HealEventSummary {
  id: string
  golden_script_id: string
  portal_domain: string | null
  trigger: string
  status: string
  diff_summary: HealDiffEntry[] | null
  created_at: string
}

export function useHealEvents(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['heal-events', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<HealEventSummary[]>(`/heal-events?${params}`)
    },
  })
}

export function useApproveHealEvent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch(`/heal-events/${id}/approve`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['heal-events'] }),
  })
}

export function useRejectHealEvent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch(`/heal-events/${id}/reject`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['heal-events'] }),
  })
}
```

- [ ] **Step 5: Approvals page**

```tsx
// frontend/src/pages/ApprovalsPage.tsx
import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { useApproveHealEvent, useHealEvents, useRejectHealEvent } from '../lib/healEvents'
import { StatusPill } from '../components/StatusPill'
import { formatRelativeTime } from '../lib/format'

export function ApprovalsPage() {
  const { user } = useAuth()
  const { data: heals, isLoading } = useHealEvents(user!.org_id, 'pending_approval')
  const approve = useApproveHealEvent()
  const reject = useRejectHealEvent()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const selected = heals?.find((h) => h.id === selectedId) ?? heals?.[0]

  return (
    <div className="p-9 pb-16 flex flex-col gap-[26px] max-w-[1280px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Heal approvals</h1>
        <div className="text-sm text-atlas-body">A healed version stays inactive until you approve it.</div>
      </div>

      {heals && heals.length > 0 && selected ? (
        <div className="grid grid-cols-[340px_1fr] gap-6 items-start">
          <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
            <div className="px-[18px] py-3.5 border-b border-atlas-border text-[11px] tracking-wider uppercase text-atlas-muted">
              Pending · {heals.length}
            </div>
            {heals.map((h) => (
              <button
                key={h.id}
                onClick={() => setSelectedId(h.id)}
                className={`w-full text-left px-[18px] py-4 border-b border-atlas-rowBorder flex flex-col gap-1.5 ${
                  h.id === selected.id ? 'bg-atlas-accentSubtle border-l-[3px] border-l-atlas-primary' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2.5">
                  <div className="text-[13.5px] font-medium tracking-tight">{h.portal_domain ?? 'Unknown portal'}</div>
                  <StatusPill status={h.status} />
                </div>
                <div className="text-xs text-atlas-body">
                  {h.diff_summary?.length ?? 0} step{h.diff_summary?.length === 1 ? '' : 's'} changed · {formatRelativeTime(h.created_at)}
                </div>
              </button>
            ))}
          </div>

          <div className="bg-atlas-card border border-atlas-border rounded-2xl p-6 flex flex-col gap-[18px]">
            <div className="flex items-start justify-between gap-5">
              <div className="flex flex-col gap-1.5">
                <div className="font-display text-xl font-semibold tracking-tight">{selected.portal_domain ?? 'Unknown portal'}</div>
                <div className="text-[13px] text-atlas-body">
                  {selected.trigger === 'runtime_auto' ? 'Detected automatically' : 'Requested on demand'} · {formatRelativeTime(selected.created_at)}
                </div>
              </div>
              <div className="flex gap-2.5 flex-none">
                <button
                  onClick={() => reject.mutate(selected.id)}
                  disabled={reject.isPending}
                  className="px-[18px] py-2.5 rounded-full border border-atlas-borderHover text-[13px] hover:bg-atlas-bg"
                >
                  Reject
                </button>
                <button
                  onClick={() => approve.mutate(selected.id)}
                  disabled={approve.isPending}
                  className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium whitespace-nowrap"
                >
                  Approve and activate
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-3">
              {(selected.diff_summary ?? []).map((entry) => (
                <div key={entry.step_index} className="grid grid-cols-2 gap-[18px]">
                  <div className="flex flex-col gap-1.5">
                    <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Step {entry.step_index + 1} — current</div>
                    <div className="p-3 rounded-xl font-mono text-[11.5px] break-all bg-atlas-diffOldBg text-atlas-diffOldText">
                      {entry.old_value}
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <div className="text-[11px] tracking-wider uppercase text-atlas-primary">Step {entry.step_index + 1} — proposed</div>
                    <div className="p-3 rounded-xl font-mono text-[11.5px] break-all bg-atlas-diffNewBg text-atlas-diffNewText">
                      {entry.new_value}
                    </div>
                  </div>
                </div>
              ))}
              {(!selected.diff_summary || selected.diff_summary.length === 0) && (
                <div className="text-[13px] text-atlas-body">No selector changes recorded for this heal event.</div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <ShieldCheck size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">Nothing to approve</div>
          <div className="text-[13.5px] text-atlas-body max-w-[400px]">We'll surface a diff here the moment a step drifts.</div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Wire the route**

Replace the `/approvals` placeholder in `App.tsx` with `<Route path="/approvals" element={<ApprovalsPage />} />`.

- [ ] **Step 7: Manual check**

Trigger a heal (`POST /golden-scripts/{id}/heal` via curl or the existing CLI, against a certified script whose portal has changed) and confirm it appears in Approvals with a real diff, and Approve/Reject both work and remove it from the pending list.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/healEvents.ts frontend/src/pages/ApprovalsPage.tsx frontend/src/App.tsx
git commit -m "feat: add heal approvals screen with real selector diffs"
```

---

### Task 12: End-to-end manual QA pass

**Files:** none (verification task)

- [ ] **Step 1: Full loop with both dev servers running**

1. `atlas create-user` a fresh user if needed; log in at `/login`.
2. As "Operations agent": create a request, record a short real flow against a test page (the fixture portal server, or any real URL), stop, confirm the review screen shows a playable video with clickable steps.
3. Switch to "Developer": confirm the new recording appears in the library; open it, generate a golden script, confirm the status updates.
4. Switch to "Approver" (or stay Developer): confirm Approvals shows "Nothing to approve" cleanly when there's nothing pending.
5. Resize the browser to a laptop width (~1280px) and confirm no horizontal scroll appears anywhere except inside the step lists' own scroll containers.

- [ ] **Step 2: Fix anything broken**

Any integration mismatch found (a field name typo, a missing loading state, a route that 404s) gets fixed here, with its own small commit — this step exists precisely because manual QA is expected to surface at least one such issue that automated tests didn't.

---

### Task 13: Frontend README + finish branch

**Files:**
- Create: `frontend/README.md`

- [ ] **Step 1: Write the README**

```markdown
# Atlas AI Console (frontend)

React + Vite + TypeScript + Tailwind console implementing the design in
`../docs/design/atlas-ai-console-mockup.dc.html`.

## Run it

```bash
npm install
npm run dev
```

Requires the backend running at `http://localhost:8000` (see `../engine/README.md`).
Set `VITE_API_BASE_URL` to point elsewhere.

## Structure

- `src/lib/` — typed API client + TanStack Query hooks, one module per backend resource.
- `src/auth/` — session (`/auth/me`) context and the route guard that redirects to `/login`.
- `src/layout/` — sidebar shell and the client-side "viewing as" role switcher (a stand-in
  for real per-role authorization, which is deferred — see the frontend design spec).
- `src/pages/agent/`, `src/pages/dev/`, `src/pages/ApprovalsPage.tsx` — the six screens.
- `src/components/VideoStepPlayer.tsx` — the video-plus-synced-steps player shared by the
  agent review screen and the developer authoring workspace.

## What's adapted from the mockup, not copied literally

- No editable recording title/description on the review screen — `Recording` has no such
  columns, and inventing an input with nowhere to save would be dishonest UI.
- No manual "Submit to developer" action — `persist_recording` already marks a linked
  request `submitted` the moment capture stops.
- The heal approval detail shows the real mechanical selector diff
  (`heal_events.diff_summary`), not narrative prose or before/after frame images —
  neither exists in the data model.
- The developer library's per-card action is "Author" (a recording, once it exists), not
  "Claim" (which applies to a request, before a recording exists) — a dedicated developer
  request-queue screen is out of scope for this pass.
```

- [ ] **Step 2: Commit**

```bash
git add frontend/README.md
git commit -m "docs: add frontend README"
```

- [ ] **Step 3: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run `npm run build`, `npm run test`, and the backend's full `pytest` suite, present the merge/PR/keep menu, act on the answer.
