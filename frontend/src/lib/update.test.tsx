import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useApplyUpdate, useUpdateCheck, useUpdateProgress } from './update'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useUpdateCheck', () => {
  it('returns the update check result', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ update_available: true, latest_version: '1.1.0', notes: 'fixes', app_asset_url: 'https://x/app.zip' }),
      }),
    ))

    const { result } = renderHook(() => useUpdateCheck(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.update_available).toBe(true)
    expect(result.current.data?.latest_version).toBe('1.1.0')
  })

  it('does not throw when the endpoint 404s (web deployment without the desktop routes)', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: 'not found' }) }),
    ))

    const { result } = renderHook(() => useUpdateCheck(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })
})

describe('useApplyUpdate', () => {
  it('POSTs to /update/apply', async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(String(url)).toContain('/update/apply')
      expect(init?.method).toBe('POST')
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useApplyUpdate(), { wrapper })
    result.current.mutate()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})

describe('useUpdateProgress', () => {
  it('fetches /update/progress only while enabled', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ phase: 'downloading_app', bytes_downloaded: 10, total_bytes: 100, error: null }),
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result, rerender } = renderHook(({ enabled }) => useUpdateProgress(enabled), {
      wrapper,
      initialProps: { enabled: false },
    })

    expect(fetchMock).not.toHaveBeenCalled()

    rerender({ enabled: true })

    await waitFor(() => expect(result.current.data?.bytes_downloaded).toBe(10))
    expect(String(fetchMock.mock.calls[0][0])).toContain('/update/progress')
  })
})
