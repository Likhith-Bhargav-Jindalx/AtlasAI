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
  app_version: string
  vendor_version: string
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

export interface UpdateProgress {
  phase: 'idle' | 'downloading_app' | 'downloading_vendor' | 'relaunching' | 'error'
  bytes_downloaded: number
  total_bytes: number | null
  error: string | null
}

export function useUpdateProgress(enabled: boolean) {
  return useQuery({
    queryKey: ['update-progress'],
    queryFn: () => apiFetch<UpdateProgress>('/update/progress'),
    enabled,
    retry: false,
    refetchInterval: 500,
  })
}
