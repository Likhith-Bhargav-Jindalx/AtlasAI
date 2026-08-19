import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingSummary {
  id: string
  status: string
  role: string
  request_id: string | null
  request_title: string | null
  lob: string | null
  action: string | null
  portal_domain: string | null
  duration_seconds: number | null
  has_video: boolean
  has_codegen_script?: boolean
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
  request_id: string | null
  title: string | null
  description: string | null
  latest_authoring_run_id: string | null
  codegen_script: string | null
  pages: RecordingPage[]
  lob: string | null
  action: string | null
  payer_scope: string | null
  payer_ids: string[] | null
  other_system_ids: string[] | null
  carc_code_id: string | null
  code_free_text: string | null
  sub_reason: string | null
  resolution_path: string[] | null
  evidence_required: string[] | null
  report_id: string | null
  date_param_logic: string | null
  output_destination: string | null
  post_processing: string | null
  scenario_description: string | null
}

export function useRecordings(orgId: string, status?: string, userId?: string) {
  return useQuery({
    queryKey: ['recordings', orgId, status, userId],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      if (userId) params.set('user_id', userId)
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
    mutationFn: (body: { org_id: string; start_url: string; request_id?: string; role?: string; user_id?: string }) =>
      apiFetch<{ capture_session_id: string }>('/recordings/start', { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ recording_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}

export interface UpdateRecordingDetailsBody {
  title?: string
  description?: string
  payer_scope?: string
  payer_ids?: string[]
  other_system_ids?: string[]
  carc_code_id?: string
  code_free_text?: string
  sub_reason?: string
  resolution_path?: string[]
  evidence_required?: string[]
  report_id?: string
  date_param_logic?: string
  output_destination?: string
  post_processing?: string
  scenario_description?: string
}

export function usePatchRecording(recordingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateRecordingDetailsBody) =>
      apiFetch<{ ok: boolean }>(`/recordings/${recordingId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording', recordingId] }),
  })
}

export function useSubmitRecording(recordingId: string | undefined) {
  return useMutation({
    mutationFn: (body: { submittedBy: string }) =>
      apiFetch<{ request_id: string }>(`/recordings/${recordingId}/submit`, {
        method: 'POST',
        body: JSON.stringify({ submitted_by: body.submittedBy }),
      }),
  })
}

export function recordingVideoUrl(recordingId: string, pageIndex: number): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/recordings/${recordingId}/video/${pageIndex}`
}
