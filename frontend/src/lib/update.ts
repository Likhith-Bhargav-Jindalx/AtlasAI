import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface UpdateCheckResult {
  update_available: boolean
  latest_version: string | null
  notes: string | null
  app_asset_url: string | null
  app_sha256: string | null
  vendor_update_required: boolean
  vendor_asset_url: string | null
  vendor_sha256: string | null
}

export function useUpdateCheck() {
  return useQuery({
    queryKey: ['update-check'],
    queryFn: () => apiFetch<UpdateCheckResult>('/update/check'),
    retry: false,
    staleTime: Infinity,
  })
}

export function useApplyUpdate() {
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>('/update/apply', { method: 'POST' }),
  })
}
