import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useLlmUsageSummary } from './llmUsage'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/llm-usage/summary?org_id=org-1') {
      return Promise.resolve({
        ok: true, status: 200,
        json: async () => ({ total_calls: 3, total_input_tokens: 100, total_output_tokens: 50, total_cost_usd: 0.12, by_day: [{ date: '2026-08-14', calls: 3, cost_usd: 0.12 }] }),
      })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('useLlmUsageSummary', () => {
  it('fetches the summary for an org', async () => {
    const { result } = renderHook(() => useLlmUsageSummary('org-1'), { wrapper })
    await waitFor(() => expect(result.current.data?.total_calls).toBe(3))
  })
})
