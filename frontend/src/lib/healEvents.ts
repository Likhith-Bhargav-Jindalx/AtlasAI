import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface HealDiffEntry {
  step_index: number
  field: string
  old_value: string
  new_value: string
}

export interface HealEventSummary {
  id: string
  golden_script_id: string
  portal_domain: string | null
  trigger: string
  status: string
  diff_summary: HealDiffEntry[] | null
  created_at: string
}

export function useHealEvents(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['heal-events', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<HealEventSummary[]>(`/heal-events?${params}`)
    },
  })
}

export function useApproveHealEvent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch(`/heal-events/${id}/approve`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['heal-events'] }),
  })
}

export function useRejectHealEvent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiFetch(`/heal-events/${id}/reject`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['heal-events'] }),
  })
}
