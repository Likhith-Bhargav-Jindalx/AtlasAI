import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { ProtectedRoute } from './ProtectedRoute'
import { RequireAdminAccess } from './RequireAdminAccess'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let role = 'member'
let canManage = false

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({
        user_id: 'u-1', org_id: 'org-1', email: 'x@x.com', role, job_function: 'developer',
        can_manage_form_options: canManage,
      }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderAtFormOptions() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/form-options']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<RequireAdminAccess />}>
                <Route path="/dev/form-options" element={<div>Form options content</div>} />
              </Route>
              <Route path="/dev" element={<div>Dev library content</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RequireAdminAccess', () => {
  it('redirects a member away', async () => {
    role = 'member'
    canManage = false
    renderAtFormOptions()
    expect(await screen.findByText('Dev library content')).toBeInTheDocument()
    expect(screen.queryByText('Form options content')).toBeNull()
  })

  it('lets an owner through', async () => {
    role = 'owner'
    canManage = false
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })

  it('lets an admin through', async () => {
    role = 'admin'
    canManage = false
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })
})

describe('RequireAdminAccess with an explicit grant', () => {
  it('lets a granted member through', async () => {
    role = 'member'
    canManage = true
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })
})
