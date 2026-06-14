import axios from 'axios';

/**
 * R1: shared API-error classification + user-facing message resolution.
 *
 * Replaces the per-screen `extractErrorMessage` helpers that fell through
 * to axios's raw English `error.message` on a transport failure. With the
 * 15s request timeout now set on the shared client, a weak-network stall
 * surfaces as a no-response error — it must read as a clear, localized
 * message, never "timeout of 15000ms exceeded".
 *
 * 401 is classified as 'auth' but is normally handled upstream by the
 * client.ts response interceptor (refresh-or-logout); a 401 only reaches a
 * screen after a failed refresh, by which point the user is being routed
 * to login.
 */
export type ApiErrorKind = 'timeout' | 'offline' | 'auth' | 'http' | 'generic';

export function classifyApiError(error: unknown): ApiErrorKind {
  if (axios.isAxiosError(error)) {
    if (!error.response) {
      // No response = the request never completed at the transport layer.
      // axios sets ECONNABORTED on a timeout (ETIMEDOUT on some platforms);
      // a plain connectivity failure is ERR_NETWORK / no code.
      const code = error.code;
      if (
        code === 'ECONNABORTED' ||
        code === 'ETIMEDOUT' ||
        /timeout/i.test(error.message ?? '')
      ) {
        return 'timeout';
      }
      return 'offline';
    }
    if (error.response.status === 401) return 'auth';
    return 'http';
  }
  return 'generic';
}

/**
 * The backend's `detail` (string, or flattened Pydantic array) if present;
 * null when there is no usable server detail. Callers that map specific
 * detail strings (e.g. the labour screen) rely on getting the raw detail.
 */
function serverDetail(error: unknown): string | null {
  if (!axios.isAxiosError(error)) return null;
  const detail = error.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d: { msg?: string; loc?: (string | number)[] }) => {
        const loc = Array.isArray(d.loc) ? d.loc.join('.') : '';
        return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '');
      })
      .join('; ');
  }
  return null;
}

/**
 * Resolve a user-facing message for an API error:
 *  - timeout / offline -> a localized transport message (never the raw axios string)
 *  - http / auth       -> the server's `detail` if present, else `fallback`
 *  - generic           -> `fallback`
 *
 * `t` is the i18next translate function, typed loosely so this module does
 * not depend on the i18next type surface.
 */
export function resolveApiErrorMessage(
  error: unknown,
  t: (key: string) => string,
  fallback: string,
): string {
  const kind = classifyApiError(error);
  if (kind === 'timeout') return t('common.error_timeout');
  if (kind === 'offline') return t('common.error_offline');
  if (kind === 'http' || kind === 'auth') {
    return serverDetail(error) ?? fallback;
  }
  return fallback;
}
