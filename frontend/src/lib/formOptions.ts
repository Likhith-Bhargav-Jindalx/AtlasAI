import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface FormOption {
  id: string
  label: string
  active: boolean
}

export type FormOptionCategory = 'client' | 'action' | 'ehr'

function queryKey(suffix: 'active' | 'all', lob: string, category: FormOptionCategory) {
  return ['form-options', suffix, lob, category] as const
}

export function useFormOptions(lob: string, category: FormOptionCategory) {
  return useQuery({
    queryKey: queryKey('active', lob, category),
    queryFn: () => apiFetch<FormOption[]>(`/form-options?lob=${lob}&category=${category}`),
  })
}

export function useAllFormOptions(lob: string, category: FormOptionCategory) {
  return useQuery({
    queryKey: queryKey('all', lob, category),
    queryFn: () => apiFetch<FormOption[]>(`/form-options/all?lob=${lob}&category=${category}`),
  })
}

export function useCreateFormOption() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { lob: string; category: FormOptionCategory; label: string }) =>
      apiFetch<FormOption>('/form-options', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['form-options'] }),
  })
}

export function useUpdateFormOption() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; label?: string; active?: boolean }) =>
      apiFetch<FormOption>(`/form-options/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['form-options'] }),
  })
}
