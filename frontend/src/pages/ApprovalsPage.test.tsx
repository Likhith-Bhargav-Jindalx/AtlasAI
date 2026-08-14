import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { ApprovalsPage } from './ApprovalsPage'

const HEAL_EVENT = {
  id: 'heal-1', golden_script_id: 'gs-1', portal_domain: 'portal.example.com', trigger: 'on_demand',
  status: 'pending_approval', created_at: new Date().toISOString(),
  diff_summary: [{ step_index: 0, field: 'selector', old_value: '#a', new_value: '#b', before_screenshot: '/x/before.png', after_screenshot: '/x/after.png' }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path.startsWith('/heal-events?')) return Promise.resolve(jsonResponse([HEAL_EVENT]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><ApprovalsPage /></AuthProvider>
    </QueryClientProvider>,
  )
}

describe('ApprovalsPage screenshots', () => {
  it('renders before/after images when the diff entry has them', async () => {
    renderPage()
    const beforeImg = await screen.findByAltText(/before/i)
    const afterImg = await screen.findByAltText(/after/i)
    expect(beforeImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/before'))
    expect(afterImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/after'))
  })
})
