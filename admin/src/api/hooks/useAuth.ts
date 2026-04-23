import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import { useAuthStore } from '../../store/auth'
import type { components } from '../types'

type LoginRequest = components['schemas']['LoginRequest']
type TokenPair = components['schemas']['TokenPair']
type UserPublic = components['schemas']['UserPublic']

export function useLogin() {
  const setTokens = useAuthStore((s) => s.setTokens)
  return useMutation({
    mutationFn: async (body: LoginRequest): Promise<TokenPair> => {
      const { data } = await api.post<TokenPair>('/auth/login', body)
      return data
    },
    onSuccess: (data) => {
      setTokens(data.access_token, data.refresh_token)
    },
  })
}

export function useLogout() {
  const clear = useAuthStore((s) => s.clear)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      try {
        await api.post('/auth/logout')
      } catch {
        // Logout is best-effort in Phase 1; always clear local state.
      }
    },
    onSettled: () => {
      clear()
      // Invalidate /auth/me so the next login fetches a fresh profile
      // rather than reading a stale cached user from the previous session.
      void qc.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
  })
}

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: async (): Promise<UserPublic> => {
      const { data } = await api.get<UserPublic>('/auth/me')
      return data
    },
    enabled: !!accessToken,
    staleTime: Infinity,
    retry: 1,
  })
}
