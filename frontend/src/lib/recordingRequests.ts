import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingRequestSummary {
  id: string
  title: string
  target_url: string
  status: string
  claimed_by: string | null
}

export function useRecordingRequests(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['recording-requests', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<RecordingRequestSummary[]>(`/recording-requests?${params}`)
    },
  })
}

export function useCreateRecordingRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { org_id: string; requested_by: string; target_url: string; title: string }) =>
      apiFetch<{ request_id: string }>('/recording-requests', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording-requests'] }),
  })
}
