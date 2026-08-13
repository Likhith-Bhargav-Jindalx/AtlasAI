import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthoringPage } from './AuthoringPage'
import type { AuthoringRunDetail } from '../../lib/authoring'

const RECORDING = {
  id: 'rec-1',
  status: 'captured',
  role: 'dev',
  pages: [{ page_index: 0, has_video: false, video_asset_id: null, duration_seconds: 12, steps: [] }],
}

function runDetail(status: string): AuthoringRunDetail {
  return {
    id: 'run-1',
    status,
    current_phase: status === 'cancelled' ? 'replay' : 'golden',
    repair_attempt: 0,
    events: [],
    golden_script: null,
    certification_runs: [],
  }
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

/** A promise whose resolution we control, so we can hold a request in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

let queryClient: QueryClient
let currentRun: AuthoringRunDetail
let startResponse: ReturnType<typeof deferred<unknown>>
let firstPoll: ReturnType<typeof deferred<unknown>>
let pollCount: number
/** Overridable so a test can make the extractions request fail. */
let extractionsResponse: () => Promise<unknown>

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  currentRun = runDetail('running')
  startResponse = deferred<unknown>()
  firstPoll = deferred<unknown>()
  pollCount = 0
  extractionsResponse = () => Promise.resolve(jsonResponse([]))

  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/recordings/rec-1' && (init?.method ?? 'GET') === 'GET') {
        return Promise.resolve(jsonResponse(RECORDING))
      }
      if (path === '/recordings/rec-1/extractions') {
        return extractionsResponse()
      }
      if (path === '/recordings/rec-1/author') return startResponse.promise
      if (path === '/authoring-runs/run-1/stop') return Promise.resolve(jsonResponse({ ok: true }))
      if (path === '/authoring-runs/run-1/resume') return Promise.resolve(jsonResponse({ ok: true }))
      if (path === '/authoring-runs/run-1') {
        pollCount += 1
        // The first poll stays in flight until the test releases it, so we can
        // inspect the window between "start succeeded" and "run data arrived".
        if (pollCount === 1) return firstPoll.promise
        return Promise.resolve(jsonResponse(currentRun))
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }),
  )
})

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dev/recordings/rec-1']}>
        <Routes>
          <Route path="/dev/recordings/:recordingId" element={<AuthoringPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function primaryButton() {
  return screen.queryByRole('button', { name: /golden script|Starting…/ })
}

/** Force an immediate poll instead of waiting out the 1.5s refetch interval. */
async function poll() {
  await act(async () => {
    await queryClient.refetchQueries({ queryKey: ['authoring-run', 'run-1'] })
  })
}

describe('AuthoringPage primary button gating', () => {
  it('stays closed from the first click until the run reaches a terminal status', async () => {
    renderPage()

    // Idle: startable.
    const startButton = await screen.findByRole('button', { name: 'Generate golden script' })
    expect(startButton).toBeEnabled()

    // Start mutation in flight.
    fireEvent.click(startButton)
    await waitFor(() => expect(primaryButton()).toHaveTextContent('Starting…'))
    expect(primaryButton()).toBeDisabled()

    // The race window: POST resolved, run id is set, but the first
    // GET /authoring-runs/run-1 has not come back yet. The button must not
    // offer a second start here.
    await act(async () => {
      startResponse.resolve(jsonResponse({ authoring_run_id: 'run-1' }))
      await startResponse.promise
    })
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).endsWith('/authoring-runs/run-1'))).toBe(true))
    expect(primaryButton()).toHaveTextContent('Starting…')
    expect(primaryButton()).toBeDisabled()

    // running: the primary button is replaced by Stop.
    await act(async () => {
      firstPoll.resolve(jsonResponse(runDetail('running')))
      await firstPoll.promise
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument())
    expect(primaryButton()).toBeNull()

    // waiting_for_developer is still active: Resume appears, still no restart.
    currentRun = runDetail('waiting_for_developer')
    await poll()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(primaryButton()).toBeNull()

    // cancelled is terminal: the button comes back, enabled, as a regenerate.
    currentRun = runDetail('cancelled')
    await poll()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Regenerate golden script' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
  })
})

describe('AuthoringPage page extractions', () => {
  it('shows an error banner when the extractions request fails', async () => {
    extractionsResponse = () => Promise.reject(new Error('extractions boom'))
    renderPage()

    expect(await screen.findByText(/Could not load page extractions/i)).toHaveTextContent(
      'extractions boom',
    )
  })
})
