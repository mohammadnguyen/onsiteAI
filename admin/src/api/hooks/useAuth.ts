import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import { useAuthStore } from '../../store/auth'
import type { components } from '../types'

type LoginRequest = components['schemas']['LoginRequest']
type TokenPair = components['schemas']['TokenPair']
type UserPublic = components['schemas']['UserPublic']

// Shared key + fetcher so Login.tsx can prime the cache via
// queryClient.fetchQuery with the identical signature useMe() uses.
// Keeping both in one place prevents key-drift bugs.
export const ME_QUERY_KEY = ['auth', 'me'] as const

export async function fetchMe(): Promise<UserPublic> {
  const { data } = await api.get<UserPublic>('/auth/me')
  return data
}

export function useLogin() {
  const setTokens = useAuthStore((s) => s.setTokens)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: LoginRequest): Promise<TokenPair> => {
      const { data } = await api.post<TokenPair>('/auth/login', body)
      return data
    },
    onSuccess: (data) => {
      // Drop any me-cache that may have leaked from a previous session
      // BEFORE the new token is written, so no component can read the
      // old user's profile against the new token between these two
      // state transitions.
      qc.removeQueries({ queryKey: ME_QUERY_KEY })
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
      // R12/H2: wipe the ENTIRE query cache at the auth boundary, not just
      // /auth/me. Cached ['jobs'] (money/margin summary), ['expenses'],
      // ['users'] and budget-summary data must not survive a user switch on
      // a shared PC. Mirrors mobile's resetSessionState() (Audit B-02) and
      // the 401 interceptor's terminalLogout(). qc is the same singleton
      // the interceptor clears (see api/queryClient.ts).
      qc.clear()
    },
  })
}

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken)
  return useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchMe,
    enabled: !!accessToken,
    staleTime: Infinity,
    retry: 1,
  })
}
