import { createContext, useContext, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, fetchMe, login as apiLogin, logout as apiLogout, type MeResponse } from '../lib/api'

interface AuthValue {
  user: MeResponse | null | undefined
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    retry: false,
    // A 401 is an expected "not logged in" state, not a transient error —
    // surface it as `data: null` instead of leaving the query stuck in
    // `error` state forever.
    throwOnError: false,
  })

  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => apiLogin(email, password),
    onSuccess: (user) => queryClient.setQueryData(['me'], user),
  })

  const logoutMutation = useMutation({
    mutationFn: apiLogout,
    onSuccess: () => queryClient.setQueryData(['me'], null),
  })

  const user = meQuery.isError && meQuery.error instanceof ApiError && meQuery.error.status === 401
    ? null
    : meQuery.data

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading: meQuery.isLoading,
        login: async (email, password) => { await loginMutation.mutateAsync({ email, password }) },
        logout: async () => { await logoutMutation.mutateAsync() },
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
