import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type AuthState = {
  accessToken: string | null
  refreshToken: string | null
  setTokens: (accessToken: string, refreshToken: string) => void
  // Update ONLY the access token — used by the 401 refresh interceptor,
  // where /auth/refresh returns a new access token but the refresh token
  // stays valid until its 30-day TTL (mirrors mobile's setAccessToken).
  setAccessToken: (accessToken: string) => void
  clear: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      setAccessToken: (accessToken) => set({ accessToken }),
      clear: () => set({ accessToken: null, refreshToken: null }),
    }),
    { name: 'sitetracker-admin-auth' },
  ),
)
