import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from 'axios';
import Constants from 'expo-constants';
import { useAuthStore } from '../store/auth';

const extraApiUrl = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl;
// Exported for the Settings → Diagnostics card (M0): shows the exact
// base URL this client resolved so on-site triage can confirm which
// environment a device points at. Read-only; nothing else consumes it.
export const apiUrl: string =
  extraApiUrl ??
  process.env.EXPO_PUBLIC_API_URL ??
  'http://127.0.0.1:8000';

// R1: cap every request so a weak-network stall can't leave the UI on a
// spinner forever. 15s tolerates slow-but-working mobile data while still
// surfacing a clear timeout (classifyApiError -> 'timeout') instead of an
// indefinite hang. Global default on the shared instance; a specific call
// can still override `timeout` per-request if a future endpoint needs it.
// A timeout is a no-response error, so the 401 refresh interceptor below
// never mistakes it for auth. Does NOT cover the Excel export, which uses
// expo-file-system downloadAsync (separate transport — tracked follow-up).
const REQUEST_TIMEOUT_MS = 15000;

export const api: AxiosInstance = axios.create({
  baseURL: apiUrl,
  timeout: REQUEST_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * Single-flight refresh promise.
 *
 * When multiple concurrent requests receive 401 simultaneously
 * (common after access-token expiry mid-session, e.g. dashboard
 * loading several queries at once), the interceptor must NOT spawn
 * one `/auth/refresh` call per failed request — that would race,
 * set stale tokens, and waste backend calls. The first 401 starts
 * a refresh; every subsequent 401 awaits the same promise. Reset
 * to null in `finally` so the next expiry cycle begins fresh.
 */
let refreshInFlight: Promise<string | null> | null = null;

/** Per-request flag indicating the request was already retried after
 *  a successful refresh. Prevents an infinite loop if the retried
 *  request also returns 401 (e.g. user account disabled between
 *  the original request and the retry). */
type RetryableConfig = InternalAxiosRequestConfig & {
  __authRetry?: boolean;
};

/**
 * Refresh-token rotation on 401 (Phase 6 followup; previously
 * deferred per the original Phase 1 comment that lived here).
 *
 * Access tokens live 60 min; refresh tokens live 30 days (backend
 * `config.py`). Without this rotation, the user was kicked to the
 * login screen at every access-token expiry — significant friction
 * surfaced during Tier 1B mobile dogfooding.
 *
 * Behaviour:
 *   1. Non-401 → propagate as-is.
 *   2. Already-retried request returning 401 → terminal logout
 *      (something is permanently wrong with the token; do not loop).
 *   3. The `/auth/refresh` endpoint itself returning 401 → terminal
 *      logout (refresh token is dead).
 *   4. No refresh token stored → terminal logout.
 *   5. Otherwise → POST stored refresh_token to `/auth/refresh`,
 *      single-flight so concurrent 401s share one call. On success,
 *      update access token in SecureStore + store + retry original
 *      request once. On a definitive auth rejection (400/401/403/422),
 *      terminal logout. On a TRANSPORT or transient failure (timeout,
 *      offline, 5xx, 408/429) the tokens are kept: the original
 *      request still fails, and the next 401 starts a fresh refresh
 *      attempt (audit A1 — clearing on any error destroyed a valid
 *      30-day refresh token on a network blip and force-logged the
 *      user out mid-shift).
 *
 * The refresh token is NOT rotated client-side; backend's `/refresh`
 * route returns only a new access token, leaving the existing
 * refresh token valid until its 30-day TTL.
 *
 * Tokens are never logged.
 */
api.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const original = err.config as RetryableConfig | undefined;
    const status = err.response?.status;

    if (status !== 401 || !original) {
      return Promise.reject(err);
    }

    if (original.__authRetry) {
      await useAuthStore.getState().clear();
      return Promise.reject(err);
    }

    if (
      typeof original.url === 'string' &&
      original.url.endsWith('/auth/refresh')
    ) {
      await useAuthStore.getState().clear();
      return Promise.reject(err);
    }

    if (!refreshInFlight) {
      const refreshToken = useAuthStore.getState().refreshToken;
      if (!refreshToken) {
        await useAuthStore.getState().clear();
        return Promise.reject(err);
      }
      refreshInFlight = (async () => {
        try {
          const r = await api.post<{
            access_token: string;
            token_type: string;
          }>('/auth/refresh', { refresh_token: refreshToken });
          await useAuthStore.getState().setAccessToken(r.data.access_token);
          return r.data.access_token;
        } catch (refreshErr) {
          // Terminal logout ONLY when the refresh endpoint itself
          // answered with a status that means the token is dead,
          // revoked, or malformed. NOT the whole 4xx range: 429
          // (rate limit) and 408 (proxy timeout) are transient and
          // must keep the session — see behaviour note 5. A
          // no-response error or 5xx also keeps the session. (The
          // 401 case also runs through the interceptor's
          // `/auth/refresh` branch above, which already clears.)
          const refreshStatus = axios.isAxiosError(refreshErr)
            ? refreshErr.response?.status
            : undefined;
          const authFatal =
            refreshStatus === 400 ||
            refreshStatus === 401 ||
            refreshStatus === 403 ||
            refreshStatus === 422;
          if (authFatal) {
            await useAuthStore.getState().clear();
          }
          return null;
        } finally {
          refreshInFlight = null;
        }
      })();
    }

    const newAccess = await refreshInFlight;
    if (!newAccess) {
      return Promise.reject(err);
    }

    original.__authRetry = true;
    return api.request(original);
  },
);
