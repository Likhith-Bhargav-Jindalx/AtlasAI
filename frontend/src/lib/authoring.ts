import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface AuthorResult {
  status: string
  script_path: string
  golden_script_id: string
}

export function useAuthorRecording(recordingId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<AuthorResult>(`/recordings/${recordingId}/author`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording', recordingId] }),
  })
}
