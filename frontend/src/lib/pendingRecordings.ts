import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface PendingPageStep {
  order_index: number
  caption: string
  offset_ms: number | null
}

export interface PendingPageDetail {
  page_index: number
  has_video: boolean
  duration_seconds: number | null
  steps: PendingPageStep[]
}

export interface PendingRecordingSummary {
  id: string
  org_id: string
  user_id: string | null
  start_url: string
  role: string
  request_id: string | null
  created_at: string
  duration_seconds: number | null
  action_count: number
  has_video: boolean
  upload_error: string | null
}

export interface PendingRecordingDetail extends PendingRecordingSummary {
  pages: PendingPageDetail[]
}

export function usePendingRecordings() {
  return useQuery({
    queryKey: ['pending-recordings'],
    queryFn: () => apiFetch<PendingRecordingSummary[]>('/pending-recordings'),
  })
}

export function usePendingRecordingDetail(pendingId: string | undefined) {
  return useQuery({
    queryKey: ['pending-recording', pendingId],
    queryFn: () => apiFetch<PendingRecordingDetail>(`/pending-recordings/${pendingId}`),
    enabled: !!pendingId,
  })
}

export function useUploadPendingRecording(pendingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ recording_id: string }>(`/pending-recordings/${pendingId}/upload`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pending-recordings'] }),
  })
}

export function useDiscardPendingRecording(pendingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<void>(`/pending-recordings/${pendingId}/discard`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pending-recordings'] }),
  })
}

export function pendingRecordingVideoUrl(pendingId: string, pageIndex: number): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/pending-recordings/${pendingId}/video/${pageIndex}`
}
