import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UpdateBanner } from './UpdateBanner'

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <UpdateBanner />
    </QueryClientProvider>,
  )
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/update/check') {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          update_available: true,
          latest_version: '1.1.0',
          notes: 'bug fixes',
          app_asset_url: 'https://x/app.zip',
        }),
      })
    }
    if (path === '/update/apply' && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  })
  vi.stubGlobal('fetch', fetchMock)
})

describe('UpdateBanner', () => {
  it('shows the available version and notes', async () => {
    renderBanner()
    expect(await screen.findByText(/1\.1\.0/)).toBeInTheDocument()
    expect(screen.getByText('bug fixes')).toBeInTheDocument()
  })

  it('renders nothing when no update is available', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: async () => ({ update_available: false }) }),
    ))
    const { container } = renderBanner()
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('dismisses on click', async () => {
    renderBanner()
    const dismissButton = await screen.findByText('Dismiss')
    fireEvent.click(dismissButton)
    expect(screen.queryByText(/1\.1\.0/)).toBeNull()
  })

  it('calls apply when "Update now" is clicked', async () => {
    renderBanner()
    const updateButton = await screen.findByText('Update now')
    fireEvent.click(updateButton)
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/update/apply'),
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })
})
