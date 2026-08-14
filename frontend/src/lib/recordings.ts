import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingSummary {
  id: string
  status: string
  role: string
  request_id: string | null
  request_title: string | null
  portal_domain: string | null
  duration_seconds: number | null
  has_video: boolean
  created_at: string
}

export interface RecordingStep {
  order_index: number
  caption: string | null
  offset_ms: number | null
}

export interface RecordingPage {
  page_index: number
  has_video: boolean
  video_asset_id: string | null
  duration_seconds: number | null
  steps: RecordingStep[]
}

export interface RecordingDetail {
  id: string
  status: string
  role: string
  codegen_script: string | null
  pages: RecordingPage[]
}

export function useRecordings(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['recordings', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<RecordingSummary[]>(`/recordings?${params}`)
    },
  })
}

export function useRecordingDetail(recordingId: string | undefined) {
  return useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => apiFetch<RecordingDetail>(`/recordings/${recordingId}`),
    enabled: !!recordingId,
  })
}

export function useStartRecording() {
  return useMutation({
    mutationFn: (body: { org_id: string; start_url: string; request_id?: string; role?: string }) =>
      apiFetch<{ capture_session_id: string }>('/recordings/start', { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ recording_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}

export function recordingVideoUrl(recordingId: string, pageIndex: number): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/recordings/${recordingId}/video/${pageIndex}`
}
