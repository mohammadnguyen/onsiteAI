/**
 * Phase 4 Batch 2 — Excel export download hook.
 *
 * Wraps ``GET /reports/expenses-excel`` so the admin UI can trigger a
 * browser download of the accountant workbook.
 *
 * Authentication: the existing axios ``api`` client carries the JWT
 * via a request interceptor; using a plain ``<a href>`` would lose the
 * header. So we ``GET`` the binary via axios + ``responseType: 'blob'``,
 * build a ``Blob`` URL, and trigger a synthetic anchor click. This is
 * the standard SPA pattern for auth'd file downloads.
 *
 * Filename: parsed from ``Content-Disposition`` so non-ASCII names
 * (e.g. ``晶晶``) survive the HTTP roundtrip. The backend sends both
 * the ``filename=`` ASCII fallback and the RFC 5987 ``filename*=UTF-8''…``
 * form; we prefer the UTF-8 form and decode it via
 * ``decodeURIComponent``.
 */
import { useMutation } from "@tanstack/react-query"
import { api } from "../client"

export type ExcelExportParams = {
  /** ISO date YYYY-MM-DD, inclusive lower bound on expense_date. */
  from_date?: string
  /** ISO date YYYY-MM-DD, inclusive upper bound on expense_date. */
  to_date?: string
  /** When true, include pending review-status rows alongside reviewed. */
  include_pending?: boolean
  /** Restrict export to a single job. */
  job_id?: string
}

/** Parse the filename from a ``Content-Disposition`` header.
 *
 * Prefers the RFC 5987 ``filename*=UTF-8''<percent-encoded>`` form
 * (preserves CJK / Unicode); falls back to the plain ``filename="…"``
 * form for older clients. Returns ``"sitetracker-export.xlsx"`` if
 * neither form is present.
 */
export function parseFilenameFromContentDisposition(
  header: string | null | undefined,
): string {
  const fallback = "sitetracker-export.xlsx"
  if (!header) return fallback
  // Try filename*=UTF-8'' first (handles non-ASCII via percent-encoding)
  const star = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      // Malformed percent-encoding — fall through to the plain form.
    }
  }
  const plain = header.match(/filename\s*=\s*"([^"]*)"/i)
  if (plain?.[1]) return plain[1]
  return fallback
}

/** Convert the params object into the query string the API expects. */
function buildQueryString(params: ExcelExportParams): Record<string, string> {
  const out: Record<string, string> = {}
  if (params.from_date) out.from_date = params.from_date
  if (params.to_date) out.to_date = params.to_date
  if (params.job_id) out.job_id = params.job_id
  // Always send include_pending explicitly — false is the default, so
  // sending "false" is a no-op on the backend but makes the intent
  // visible in network logs during debugging.
  out.include_pending = params.include_pending ? "true" : "false"
  return out
}

/**
 * TanStack mutation that downloads the Excel export and triggers a
 * browser save. The mutate function takes the export filters; the
 * promise resolves to the saved filename on success.
 */
export function useExpensesExcel() {
  return useMutation({
    mutationFn: async (params: ExcelExportParams): Promise<string> => {
      const response = await api.get<Blob>("/reports/expenses-excel", {
        params: buildQueryString(params),
        responseType: "blob",
      })
      const filename = parseFilenameFromContentDisposition(
        response.headers["content-disposition"],
      )
      const blob = response.data
      const url = URL.createObjectURL(blob)
      try {
        const anchor = document.createElement("a")
        anchor.href = url
        anchor.download = filename
        document.body.appendChild(anchor)
        anchor.click()
        document.body.removeChild(anchor)
      } finally {
        // Defer revocation so the browser has time to start the
        // download before the URL becomes invalid.
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
      return filename
    },
  })
}
