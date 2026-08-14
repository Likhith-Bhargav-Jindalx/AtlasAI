import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useClaimRecordingRequest } from './recordingRequests'

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/recording-requests/req-1/claim' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({ developer_id: 'dev-1' })
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('useClaimRecordingRequest', () => {
  it('POSTs the developer id and invalidates the requests query', async () => {
    const { result } = renderHook(() => useClaimRecordingRequest(), { wrapper })
    result.current.mutate({ requestId: 'req-1', developerId: 'dev-1' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
