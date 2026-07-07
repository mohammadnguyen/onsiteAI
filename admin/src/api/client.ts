import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../store/auth'
import { queryClient } from './queryClient'

const baseURL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

// R11: cap every request so a weak-network stall can't leave the UI on a
// spinner forever. 15s tolerates slow-but-working connections while still
// surfacing a clear timeout instead of an indefinite hang. Mirrors
// mobile/src/api/client.ts (REQUEST_TIMEOUT_MS). A timeout is a
// no-response error, so the 401 refresh interceptor below never mistakes
// it for auth.
const REQUEST_TIMEOUT_MS = 15000

export const api = axios.create({
  baseURL,
  timeout: REQUEST_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const { accessToken } = useAuthStore.getState()
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

/**
 * Terminal logout: clear tokens AND the entire React Query cache.
 *
 * R12/H2: cached ['jobs'] (money/margin summary), ['expenses'],
 * ['users'] and budget-summary data must never survive the auth
 * boundary — on a shared PC the next login could otherwise read the
 * previous user's money data before a refetch completes. Token clearing
 * stays in useAuthStore.clear(); this wraps it with queryClient.clear()
 * so every terminal path clears both. Mirrors mobile's
 * resetSessionState() (Audit B-02).
 */
function terminalLogout(): void {
  useAuthStore.getState().clear()
  queryClient.clear()
}

/**
 * Single-flight refresh promise.
 *
 * When several concurrent requests hit 401 at once (common right after
 * access-token expiry, when a page fires multiple queries), the
 * interceptor must NOT spawn one /auth/refresh per failed request —
 * that races, sets stale tokens, and wastes backend calls. The first
 * 401 starts a refresh; every subsequent 401 awaits the same promise.
 * Reset to null in `finally` so the next expiry cycle starts fresh.
 */
let refreshInFlight: Promise<string | null> | null = null

/** Per-request flag: the request was already retried once after a
 *  successful refresh. Prevents an infinite loop if the retry also 401s
 *  (e.g. the account was disabled between the original call and retry). */
type RetryableConfig = InternalAxiosRequestConfig & {
  __authRetry?: boolean
}

/**
 * R10/S3: refresh-token rotation on 401 (ports mobile's interceptor).
 *
 * Access tokens live 60 min; refresh tokens live 30 days (backend
 * config). Without this, the user was hard-logged-out at every
 * access-token expiry. Behaviour:
 *   1. Non-401 → propagate as-is.
 *   2. Already-retried request 401 → terminal logout (do not loop).
 *   3. /auth/refresh itself 401 → terminal logout (refresh token dead).
 *   4. No refresh token stored → terminal logout.
 *   5. Otherwise → POST the stored refresh_token to /auth/refresh,
 *      single-flight so concurrent 401s share one call. On success,
 *      update the access token in the store + retry the original request
 *      once. Terminal logout only on a definitive auth rejection
 *      (400/401/403/422); a transport/transient failure (timeout,
 *      offline, 5xx, 408/429) KEEPS the session so a network blip can't
 *      destroy a valid 30-day refresh token (mobile audit A1).
 */
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetryableConfig | undefined
    const status = error.response?.status

    if (status !== 401 || !original) {
      return Promise.reject(error)
    }

    if (original.__authRetry) {
      terminalLogout()
      return Promise.reject(error)
    }

    if (
      typeof original.url === 'string' &&
      original.url.endsWith('/auth/refresh')
    ) {
      terminalLogout()
      return Promise.reject(error)
    }

    if (!refreshInFlight) {
      const refreshToken = useAuthStore.getState().refreshToken
      if (!refreshToken) {
        terminalLogout()
        return Promise.reject(error)
      }
      refreshInFlight = (async () => {
        try {
          const r = await api.post<{ access_token: string; token_type: string }>(
            '/auth/refresh',
            { refresh_token: refreshToken },
          )
          useAuthStore.getState().setAccessToken(r.data.access_token)
          return r.data.access_token
        } catch (refreshErr) {
          // Terminal logout ONLY when the refresh endpoint itself answered
          // with a status meaning the token is dead/revoked/malformed. NOT
          // the whole 4xx range: 429 (rate limit) and 408 (proxy timeout)
          // are transient and must keep the session; a no-response error or
          // 5xx likewise keeps it.
          const refreshStatus = axios.isAxiosError(refreshErr)
            ? refreshErr.response?.status
            : undefined
          const authFatal =
            refreshStatus === 400 ||
            refreshStatus === 401 ||
            refreshStatus === 403 ||
            refreshStatus === 422
          if (authFatal) {
            terminalLogout()
          }
          return null
        } finally {
          refreshInFlight = null
        }
      })()
    }

    const newAccess = await refreshInFlight
    if (!newAccess) {
      return Promise.reject(error)
    }

    original.__authRetry = true
    return api.request(original)
  },
)

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d: { msg?: string; loc?: (string | number)[] }) => {
          const loc = Array.isArray(d.loc) ? d.loc.join('.') : ''
          return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '')
        })
        .join('; ')
    }
    if (error.message) return error.message
  }
  if (error instanceof Error) return error.message
  return 'Unknown error'
}
