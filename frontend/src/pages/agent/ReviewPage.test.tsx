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

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    if (path === '/recordings/rec-1' && (init?.method ?? 'GET') === 'GET') return Promise.resolve(jsonResponse(RECORDING))
    if (path === '/recordings/rec-1/submit' && init?.method === 'POST') return Promise.resolve(jsonResponse({ request_id: 'req-9' }))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/recordings/rec-1']}>
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
    renderPage()
    const submitButton = await screen.findByRole('button', { name: /submit to developer/i })
    fireEvent.click(submitButton)
    await waitFor(() => expect(screen.getByText(/submitted/i)).toBeInTheDocument())
  })
})
