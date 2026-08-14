import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { ProtectedRoute } from '../../auth/ProtectedRoute'
import { CostDashboardPage } from './CostDashboardPage'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path === '/llm-usage/summary?org_id=org-1') {
      return Promise.resolve(jsonResponse({ total_calls: 3, total_input_tokens: 100, total_output_tokens: 50, total_cost_usd: 0.20, by_day: [{ date: '2026-08-14', calls: 2, cost_usd: 0.12 }, { date: '2026-08-13', calls: 1, cost_usd: 0.08 }] }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/costs']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/dev/costs" element={<CostDashboardPage />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CostDashboardPage', () => {
  it('renders totals and the per-day breakdown', async () => {
    renderPage()
    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(screen.getByText('$0.12')).toBeInTheDocument()
    expect(screen.getByText('2026-08-14')).toBeInTheDocument()
  })
})
