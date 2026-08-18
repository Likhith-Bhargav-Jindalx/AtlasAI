import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { ProtectedRoute } from '../auth/ProtectedRoute'
import { AppLayout } from './AppLayout'
import { RoleProvider } from './RoleContext'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let jobFunction = 'developer'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(
        jsonResponse({ user_id: 'u-1', org_id: 'org-1', email: 'x@x.com', role: 'member', job_function: jobFunction }),
      )
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderLayout() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<RoleProvider><AppLayout /></RoleProvider>}>
                <Route path="/" element={<div>page content</div>} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AppLayout viewing-as switcher', () => {
  it('locks an operations agent to a single non-interactive "Operations agent" row', async () => {
    jobFunction = 'operations_agent'
    renderLayout()
    await screen.findByText('page content')
    expect(screen.queryByRole('button', { name: 'Operations agent' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Developer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Approver' })).toBeNull()
    expect(screen.getAllByText('Operations agent').length).toBeGreaterThan(0)
  })

  it('lets a developer freely switch between all three views', async () => {
    jobFunction = 'developer'
    renderLayout()
    expect(await screen.findByRole('button', { name: 'Operations agent' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Developer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approver' })).toBeInTheDocument()

    screen.getByRole('button', { name: 'Operations agent' }).click()
    expect(await screen.findByText('Request inbox')).toBeInTheDocument()
  })
})
