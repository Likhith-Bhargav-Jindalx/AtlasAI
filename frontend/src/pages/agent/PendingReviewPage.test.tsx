import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { PendingReviewPage } from './PendingReviewPage'

const PENDING_DETAIL = {
  id: 'pending-1', org_id: 'org-1', user_id: 'agent-1', start_url: 'https://x.test',
  role: 'demo', request_id: null, created_at: '2026-11-13T10:00:00+00:00',
  duration_seconds: 42, action_count: 1, has_video: false, upload_error: null,
  pages: [{ page_index: 0, has_video: false, duration_seconds: 42, steps: [{ order_index: 0, caption: 'Click #go', offset_ms: 0 }] }],
}

let detail: unknown = PENDING_DETAIL

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  detail = PENDING_DETAIL
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    }
    if (path === '/pending-recordings/pending-1' && (init?.method ?? 'GET') === 'GET') {
      return Promise.resolve(jsonResponse(detail))
    }
    if (path === '/pending-recordings/pending-1/upload' && init?.method === 'POST') {
      return Promise.resolve(jsonResponse({ recording_id: 'rec-9' }))
    }
    if (path === '/pending-recordings/pending-1/discard' && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 204, json: async () => undefined })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/pending/pending-1']}>
          <Routes>
            <Route path="/agent/pending/:pendingId" element={<PendingReviewPage />} />
            <Route path="/agent/recordings/:recordingId" element={<div>Review page for rec-9</div>} />
            <Route path="/agent/pending" element={<div>Pending queue</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('PendingReviewPage', () => {
  it('shows every tab of a multi-tab recording, not just the main window', async () => {
    detail = {
      ...PENDING_DETAIL,
      pages: [
        { page_index: 0, has_video: false, duration_seconds: 42, steps: [{ order_index: 0, caption: 'Click #go', offset_ms: 0 }] },
        { page_index: 1, has_video: false, duration_seconds: 5, steps: [
          { order_index: 0, caption: 'Open new tab at https://x.test/b', offset_ms: 0 },
          { order_index: 1, caption: 'Click #search', offset_ms: 900 },
        ] },
      ],
    }
    renderPage()
    await waitFor(() => expect(screen.getByText('Click #search')).toBeInTheDocument())
    expect(screen.getByText('Main window')).toBeInTheDocument()
    expect(screen.getByText('Tab 2')).toBeInTheDocument()
    expect(screen.getByText('Click #go')).toBeInTheDocument()
    expect(screen.getByText(/3 actions/)).toBeInTheDocument()
    expect(screen.getByText(/2 tabs/)).toBeInTheDocument()
  })

  it('uploads and navigates to the real review page on success', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('Click #go')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Upload'))

    await waitFor(() => expect(screen.getByText('Review page for rec-9')).toBeInTheDocument())
  })

  it('cancelling the discard confirmation does not discard anything', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('Click #go')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Discard'))
    await waitFor(() => expect(screen.getByText('Discard this recording?')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Cancel'))

    expect(screen.queryByText('Discard this recording?')).not.toBeInTheDocument()
    expect(screen.getByText('Click #go')).toBeInTheDocument()
  })

  it('confirming the discard dialog discards and returns to the pending queue', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('Click #go')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Discard'))
    await waitFor(() => expect(screen.getByText('Discard this recording?')).toBeInTheDocument())

    fireEvent.click(screen.getAllByText('Discard')[1])

    await waitFor(() => expect(screen.getByText('Pending queue')).toBeInTheDocument())
  })
})
