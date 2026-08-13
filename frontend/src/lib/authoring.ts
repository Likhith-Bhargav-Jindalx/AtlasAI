import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface AuthoringRunEvent {
  phase: string
  step_index: number | null
  level: string
  message: string
}

export interface CertificationRun {
  run_number: number
  status: string
  failure_evidence_blob_ref: string | null
}

export interface GoldenScriptSummary {
  id: string
  version: number
  status: string
  script_text: string | null
}

export interface AuthoringRunDetail {
  id: string
  status: string
  current_phase: string
  repair_attempt: number
  events: AuthoringRunEvent[]
  golden_script: GoldenScriptSummary | null
  certification_runs: CertificationRun[]
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status)
}

export function useStartAuthoringRun(recordingId: string) {
  return useMutation({
    mutationFn: () =>
      apiFetch<{ authoring_run_id: string }>(`/recordings/${recordingId}/author`, { method: 'POST' }),
  })
}

export function useAuthoringRun(authoringRunId: string | undefined) {
  return useQuery({
    queryKey: ['authoring-run', authoringRunId],
    queryFn: () => apiFetch<AuthoringRunDetail>(`/authoring-runs/${authoringRunId}`),
    enabled: !!authoringRunId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data && isTerminalStatus(data.status)) return false
      return 1500
    },
  })
}

export function useResumeAuthoringRun(authoringRunId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>(`/authoring-runs/${authoringRunId}/resume`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['authoring-run', authoringRunId] }),
  })
}

export function useStopAuthoringRun(authoringRunId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>(`/authoring-runs/${authoringRunId}/stop`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['authoring-run', authoringRunId] }),
  })
}
