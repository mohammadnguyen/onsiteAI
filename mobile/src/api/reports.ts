import * as FileSystem from 'expo-file-system/legacy';

import { apiUrl } from './client';
import { useAuthStore } from '../store/auth';
import { todayISO } from '../util/dates';

/**
 * A4: accountant Excel export download.
 *
 * Why a bespoke download path instead of the shared axios `api`: the
 * response is a binary StreamingResponse, and the share step needs the
 * bytes on the local filesystem. `expo-file-system`'s legacy
 * `downloadAsync` streams the response straight to a file AND returns the
 * HTTP status — which is what lets us map 400 / 401 / 403 to distinct,
 * clear messages.
 *
 * Two consequences of bypassing axios, handled deliberately here:
 *   1. The Bearer token is attached MANUALLY (the request interceptor in
 *      `client.ts` never runs for this request).
 *   2. A 401 does NOT get the response interceptor's silent
 *      refresh-and-retry. An expired session surfaces as an explicit
 *      `session_expired` error the screen shows the user (operator
 *      decision) rather than being auto-refreshed.
 *
 * `downloadAsync` writes the response body to disk regardless of status,
 * so on a non-200 the file holds the JSON error body, never an xlsx — we
 * delete it before returning so a stale error file can never reach the
 * share sheet.
 *
 * The legacy import (`expo-file-system/legacy`) is the officially
 * supported path for the status-returning `downloadAsync`; the SDK 54
 * `File` API returns only a path and would force us to sniff thrown-error
 * shapes to tell a 401 from a 403.
 */

export type ExportErrorKind =
  | 'session_expired' // 401 — token missing or rejected
  | 'forbidden' // 403 — not an admin
  | 'bad_dates' // 400 — backend rejected the date range
  | 'network' // request never completed (connectivity / IO)
  | 'generic'; // any other non-200, or no writable cache dir

export class ExportError extends Error {
  readonly kind: ExportErrorKind;
  readonly status?: number;
  constructor(kind: ExportErrorKind, status?: number) {
    super(kind);
    this.name = 'ExportError';
    this.kind = kind;
    this.status = status;
  }
}

export type ExportParams = {
  /** ISO YYYY-MM-DD; caller validates the user input before calling. */
  fromDate?: string;
  /** ISO YYYY-MM-DD. */
  toDate?: string;
  /** When false (default), only reviewed expenses are exported. */
  includePending: boolean;
};

export type ExportResult = { uri: string; mimeType: string; uti: string };

// Excel 2007+ — the same MIME the backend streams; UTI is the iOS form.
const XLSX_MIME =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const XLSX_UTI = 'org.openxmlformats.spreadsheetml.sheet';

function buildUrl(params: ExportParams): string {
  // Built by hand (not URLSearchParams) to avoid RN polyfill quirks.
  const parts: string[] = [];
  if (params.fromDate) {
    parts.push(`from_date=${encodeURIComponent(params.fromDate)}`);
  }
  if (params.toDate) {
    parts.push(`to_date=${encodeURIComponent(params.toDate)}`);
  }
  // Send the flag only when ON: the backend default is reviewed-only, so
  // OFF exercises the default path with a minimal URL.
  if (params.includePending) {
    parts.push('include_pending=true');
  }
  const qs = parts.join('&');
  return `${apiUrl}/reports/expenses-excel${qs ? `?${qs}` : ''}`;
}

/**
 * Download the accountant workbook to a local cache file and return its
 * URI for sharing. Throws {@link ExportError} on any non-200 or failure;
 * the caller maps `kind` to a localized message.
 */
export async function downloadExpensesExcel(
  params: ExportParams,
): Promise<ExportResult> {
  const token = useAuthStore.getState().accessToken;
  if (!token) throw new ExportError('session_expired');

  const dir = FileSystem.cacheDirectory;
  if (!dir) {
    // No writable cache dir (e.g. web). The screen guards with
    // Sharing.isAvailableAsync first; fail explicitly if we still get here.
    throw new ExportError('generic');
  }

  const fileUri = `${dir}sitetracker-expenses-${todayISO()}.xlsx`;
  const url = buildUrl(params);

  let result: FileSystem.FileSystemDownloadResult;
  try {
    result = await FileSystem.downloadAsync(url, fileUri, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // A throw here is connectivity / IO — never an HTTP status.
    throw new ExportError('network');
  }

  if (result.status === 200) {
    return { uri: result.uri, mimeType: XLSX_MIME, uti: XLSX_UTI };
  }

  // Non-200: the file on disk is the JSON error body — bin it so it can
  // never be shared, then map the status to a typed error.
  try {
    await FileSystem.deleteAsync(result.uri, { idempotent: true });
  } catch {
    /* best-effort cleanup; deletion failure must not mask the real error */
  }

  if (result.status === 401) throw new ExportError('session_expired', 401);
  if (result.status === 403) throw new ExportError('forbidden', 403);
  if (result.status === 400) throw new ExportError('bad_dates', 400);
  throw new ExportError('generic', result.status);
}
