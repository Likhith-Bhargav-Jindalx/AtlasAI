import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface PageExtraction {
  id: string
  page_index: number
  url: string
  data: { fields: Record<string, unknown>; tables: unknown[] }
  extracted_at: string
}

export function useRecordingExtractions(recordingId: string | undefined) {
  return useQuery({
    queryKey: ['recording-extractions', recordingId],
    queryFn: () => apiFetch<PageExtraction[]>(`/recordings/${recordingId}/extractions`),
    enabled: !!recordingId,
  })
}
