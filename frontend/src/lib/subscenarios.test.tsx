import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useAllSubscenarios, useCreateSubscenario, useSubscenarios, useUpdateSubscenario } from './subscenarios'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useSubscenarios', () => {
  it('fetches active subscenarios for an action id', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios?action_id=action-1') {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: '1', label: 'Bundled Denial', active: true }] })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useSubscenarios('action-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '1', label: 'Bundled Denial', active: true }])
  })

  it('does not fetch when actionId is null', () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('should not fetch'))))
    const { result } = renderHook(() => useSubscenarios(null), { wrapper })
    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useAllSubscenarios', () => {
  it('fetches from the /all endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios/all?action_id=action-1') {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => [{ id: '1', label: 'Active', active: true }, { id: '2', label: 'Inactive', active: false }],
        })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useAllSubscenarios('action-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(2)
  })
})

describe('useCreateSubscenario', () => {
  it('POSTs the new subscenario', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios' && init?.method === 'POST') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ action_id: 'action-1', label: 'New Subscenario' })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Subscenario', active: true }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useCreateSubscenario(), { wrapper })
    result.current.mutate({ action_id: 'action-1', label: 'New Subscenario' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})

describe('useUpdateSubscenario', () => {
  it('PATCHes the subscenario', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/subscenarios/3' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ active: false })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Subscenario', active: false }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useUpdateSubscenario(), { wrapper })
    result.current.mutate({ id: '3', active: false })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
