import { useMutation } from '@tanstack/react-query'
import { api } from '../client'
import { useAuthStore } from '../../store/auth'
import type { components } from '../types'

type LoginRequest = components['schemas']['LoginRequest']
type TokenPair = components['schemas']['TokenPair']

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
    },
  })
}
