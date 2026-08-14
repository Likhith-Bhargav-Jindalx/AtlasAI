import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { AgentLibraryPage } from './AgentLibraryPage'

const RECORDINGS = [
  {
    id: 'rec-1', status: 'captured', role: 'demo', request_id: null, request_title: null,
    portal_domain: 'portal.example.com', duration_seconds: 42, has_video: true,
    has_codegen_script: true, created_at: new Date().toISOString(),
  },
]

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'user-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    if (path.startsWith('/recordings?')) return Promise.resolve(jsonResponse(RECORDINGS))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/library']}>
          <Routes>
            <Route path="/agent/library" element={<AgentLibraryPage />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('AgentLibraryPage', () => {
  it('renders recording cards without a codegen badge', async () => {
    renderPage()
    const elements = await screen.findAllByText('portal.example.com')
    expect(elements.length).toBeGreaterThan(0)
    expect(screen.queryByText(/playwright script/i)).not.toBeInTheDocument()
  })
})
