import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { ReviewPage } from './ReviewPage'

const RECORDING = {
  id: 'rec-1', status: 'captured', role: 'demo', request_id: null,
  title: null, description: null, latest_authoring_run_id: null, codegen_script: null,
  pages: [{ page_index: 0, has_video: false, video_asset_id: null, duration_seconds: 10, steps: [] }],
}

const LINKED_RECORDING = {
  id: 'rec-2', status: 'captured', role: 'demo', request_id: 'req-1',
  title: null, description: null, latest_authoring_run_id: null, codegen_script: null,
  pages: [{ page_index: 0, has_video: false, video_asset_id: null, duration_seconds: 10, steps: [] }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    if (path === '/recordings/rec-1' && (init?.method ?? 'GET') === 'GET') return Promise.resolve(jsonResponse(RECORDING))
    if (path === '/recordings/rec-2' && (init?.method ?? 'GET') === 'GET') return Promise.resolve(jsonResponse(LINKED_RECORDING))
    if (path === '/recordings/rec-1/submit' && init?.method === 'POST') return Promise.resolve(jsonResponse({ request_id: 'req-9' }))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage(recordingId: string = 'rec-1') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[`/agent/recordings/${recordingId}`]}>
          <Routes>
            <Route path="/agent/recordings/:recordingId" element={<ReviewPage />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('ReviewPage submit-to-developer', () => {
  it('shows the submit button for an unlinked recording and calls submit on click', async () => {
    renderPage('rec-1')
    const submitButton = await screen.findByRole('button', { name: /submit to developer/i })
    fireEvent.click(submitButton)
    await waitFor(() => expect(screen.getByText(/submitted/i)).toBeInTheDocument())
  })

  it('does not show the submit button or editable fields for a linked recording', async () => {
    renderPage('rec-2')
    // Wait for the page to load
    await screen.findByText(/Recording review/i)
    // Verify the submit button is absent
    expect(screen.queryByRole('button', { name: /submit to developer/i })).toBeNull()
    // Verify the title and description inputs are absent
    expect(screen.queryByPlaceholderText(/Title/i)).toBeNull()
    expect(screen.queryByPlaceholderText(/Description/i)).toBeNull()
  })
})
