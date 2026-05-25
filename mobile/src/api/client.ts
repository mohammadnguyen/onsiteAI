import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from 'axios';
import Constants from 'expo-constants';
import { useAuthStore } from '../store/auth';

const extraApiUrl = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl;
const apiUrl: string =
  extraApiUrl ??
  process.env.EXPO_PUBLIC_API_URL ??
  'http://127.0.0.1:8000';

export const api: AxiosInstance = axios.create({
  baseURL: apiUrl,
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
 *      request once. On failure, terminal logout.
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
        } catch {
          await useAuthStore.getState().clear();
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
