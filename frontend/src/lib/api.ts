const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, body.detail ?? response.statusText)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface MeResponse {
  user_id: string
  org_id: string
  email: string
  role: string
  job_function: string | null
}

export function login(email: string, password: string) {
  return apiFetch<MeResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
}

export function logout() {
  return apiFetch<{ ok: boolean }>('/auth/logout', { method: 'POST' })
}

export function fetchMe() {
  return apiFetch<MeResponse>('/auth/me')
}
