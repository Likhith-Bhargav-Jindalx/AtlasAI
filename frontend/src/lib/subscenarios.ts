import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface Subscenario {
  id: string
  label: string
  active: boolean
}

function queryKey(suffix: 'active' | 'all', actionId: string | null) {
  return ['subscenarios', suffix, actionId] as const
}

export function useSubscenarios(actionId: string | null) {
  return useQuery({
    queryKey: queryKey('active', actionId),
    queryFn: () => apiFetch<Subscenario[]>(`/subscenarios?action_id=${actionId}`),
    enabled: !!actionId,
  })
}

export function useAllSubscenarios(actionId: string | null) {
  return useQuery({
    queryKey: queryKey('all', actionId),
    queryFn: () => apiFetch<Subscenario[]>(`/subscenarios/all?action_id=${actionId}`),
    enabled: !!actionId,
  })
}

export function useCreateSubscenario() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { action_id: string; label: string }) =>
      apiFetch<Subscenario>('/subscenarios', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscenarios'] }),
  })
}

export function useUpdateSubscenario() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; label?: string; active?: boolean }) =>
      apiFetch<Subscenario>(`/subscenarios/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscenarios'] }),
  })
}
