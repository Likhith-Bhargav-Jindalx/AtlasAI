import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface LlmUsageDay {
  date: string
  calls: number
  cost_usd: number
}

export interface LlmUsageSummary {
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  by_day: LlmUsageDay[]
}

export function useLlmUsageSummary(orgId: string) {
  return useQuery({
    queryKey: ['llm-usage-summary', orgId],
    queryFn: () => apiFetch<LlmUsageSummary>(`/llm-usage/summary?org_id=${orgId}`),
  })
}
