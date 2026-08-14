import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { ProtectedRoute } from '../../auth/ProtectedRoute'
import { RequestQueuePage } from './RequestQueuePage'

const OPEN_REQUEST = { id: 'req-1', title: 'Check eligibility', target_url: 'https://portal.example.com', status: 'open', claimed_by: null }
let claimed = false

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  claimed = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path.startsWith('/recording-requests?')) {
      // Check if this is a status=open or status=submitted query
      const isOpen = path.includes('status=open')
      const isSubmitted = path.includes('status=submitted')
      if (isOpen) {
        return Promise.resolve(jsonResponse(claimed ? [] : [OPEN_REQUEST]))
      }
      if (isSubmitted) {
        // After claiming, the request appears in submitted with claimed_by set
        return Promise.resolve(jsonResponse(claimed ? [{ ...OPEN_REQUEST, claimed_by: 'dev-1' }] : []))
      }
      return Promise.resolve(jsonResponse([]))
    }
    if (path === '/recording-requests/req-1/claim' && init?.method === 'POST') {
      claimed = true
      return Promise.resolve(jsonResponse({ ok: true }))
    }
    if (path.startsWith('/recordings?')) return Promise.resolve(jsonResponse([]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/requests']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/dev/requests" element={<RequestQueuePage />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RequestQueuePage', () => {
  it('claims a request and moves it into the claimed-by-me section', async () => {
    renderPage()
    const claimButton = await screen.findByRole('button', { name: /claim/i })
    fireEvent.click(claimButton)
    await waitFor(() => expect(screen.getByText(/waiting for a recording/i)).toBeInTheDocument())
  })
})
