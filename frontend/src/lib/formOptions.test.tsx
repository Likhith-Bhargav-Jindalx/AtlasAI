import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useAllFormOptions, useCreateFormOption, useFormOptions, useUpdateFormOption } from './formOptions'

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('useFormOptions', () => {
  it('fetches active options for a lob/category', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options?lob=AR&category=action') {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: '1', label: 'Claim Submission', active: true }] })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useFormOptions('AR', 'action'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([{ id: '1', label: 'Claim Submission', active: true }])
  })
})

describe('useAllFormOptions', () => {
  it('fetches from the /all endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options/all?lob=AR&category=action') {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => [{ id: '1', label: 'Active', active: true }, { id: '2', label: 'Inactive', active: false }],
        })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useAllFormOptions('AR', 'action'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(2)
  })
})

describe('useCreateFormOption', () => {
  it('POSTs the new option', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options' && init?.method === 'POST') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ lob: 'AR', category: 'action', label: 'New Action' })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Action', active: true }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useCreateFormOption(), { wrapper })
    result.current.mutate({ lob: 'AR', category: 'action', label: 'New Action' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})

describe('useUpdateFormOption', () => {
  it('PATCHes the option', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/form-options/3' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body).toEqual({ active: false })
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: '3', label: 'New Action', active: false }) })
      }
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    const { result } = renderHook(() => useUpdateFormOption(), { wrapper })
    result.current.mutate({ id: '3', active: false })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
