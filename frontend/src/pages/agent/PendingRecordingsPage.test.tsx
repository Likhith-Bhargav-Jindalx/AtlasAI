import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { PendingRecordingsPage } from './PendingRecordingsPage'

const PENDING = [
  {
    id: 'pending-1', org_id: 'org-1', user_id: 'agent-1', start_url: 'https://x.test',
    role: 'demo', request_id: null, created_at: '2026-11-13T10:00:00+00:00',
    duration_seconds: 42, action_count: 5, has_video: true, upload_error: null,
  },
]

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    }
    if (path === '/pending-recordings') return Promise.resolve(jsonResponse(PENDING))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/pending']}>
          <PendingRecordingsPage />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('PendingRecordingsPage', () => {
  it('lists pending recordings from the local queue', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('https://x.test')).toBeInTheDocument())
  })
})
