import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { ProtectedRoute } from './ProtectedRoute'
import { RequireDeveloperAccess } from './RequireDeveloperAccess'

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

function renderAtDevCosts() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/costs']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<RequireDeveloperAccess />}>
                <Route path="/dev/costs" element={<div>Cost dashboard content</div>} />
              </Route>
              <Route path="/agent" element={<div>Agent inbox content</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RequireDeveloperAccess', () => {
  it('redirects an operations agent away from a developer-only route', async () => {
    jobFunction = 'operations_agent'
    renderAtDevCosts()
    expect(await screen.findByText('Agent inbox content')).toBeInTheDocument()
    expect(screen.queryByText('Cost dashboard content')).toBeNull()
  })

  it('lets a developer reach the route normally', async () => {
    jobFunction = 'developer'
    renderAtDevCosts()
    expect(await screen.findByText('Cost dashboard content')).toBeInTheDocument()
  })
})
