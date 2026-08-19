import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface SystemOption {
  id: string
  name: string
  category: string | null
}

export interface PayerOption {
  id: string
  name: string
}

export interface ClientOption {
  id: string
  name: string
}

export interface CarcCodeOption {
  id: string
  code: string
  description: string | null
  is_multi_use: boolean
}

export function useSystems() {
  return useQuery({ queryKey: ['master-data', 'systems'], queryFn: () => apiFetch<SystemOption[]>('/master-data/systems') })
}

export function usePayers() {
  return useQuery({ queryKey: ['master-data', 'payers'], queryFn: () => apiFetch<PayerOption[]>('/master-data/payers') })
}

export function useClients() {
  return useQuery({ queryKey: ['master-data', 'clients'], queryFn: () => apiFetch<ClientOption[]>('/master-data/clients') })
}

export function useCarcCodes() {
  return useQuery({ queryKey: ['master-data', 'carc-codes'], queryFn: () => apiFetch<CarcCodeOption[]>('/master-data/carc-codes') })
}
