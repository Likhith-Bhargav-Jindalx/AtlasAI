import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingRequestSummary {
  id: string
  title: string
  target_url: string
  status: string
  claimed_by: string | null
  lob: string | null
  category: string | null
  action: string | null
  system_id: string | null
  client_scope: string[] | null
  reuse_scope: string | null
  ehr: string | null
  specific_to: string | null
}

export interface CreateRecordingRequestBody {
  org_id: string
  requested_by: string
  target_url: string
  title: string
  description?: string
  lob?: string
  category?: string
  action?: string
  system_id?: string
  client_scope?: string[]
  reuse_scope?: string
  ehr?: string
  specific_to?: string
}

export function useRecordingRequests(orgId: string, status?: string, filters?: { lob?: string; systemId?: string; clientId?: string }) {
  return useQuery({
    queryKey: ['recording-requests', orgId, status, filters],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      if (filters?.lob) params.set('lob', filters.lob)
      if (filters?.systemId) params.set('system_id', filters.systemId)
      if (filters?.clientId) params.set('client_id', filters.clientId)
      return apiFetch<RecordingRequestSummary[]>(`/recording-requests?${params}`)
    },
  })
}

export function useCreateRecordingRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateRecordingRequestBody) =>
      apiFetch<{ request_id: string }>('/recording-requests', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording-requests'] }),
  })
}

export function useClaimRecordingRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ requestId, developerId }: { requestId: string; developerId: string }) =>
      apiFetch<{ ok: boolean }>(`/recording-requests/${requestId}/claim`, {
        method: 'POST',
        body: JSON.stringify({ developer_id: developerId }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording-requests'] }),
  })
}
