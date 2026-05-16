import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useJobAuditTrail, type JobAuditRow } from '../api/hooks/useJobs'
import { extractErrorMessage } from '../api/client'

/**
 * Job Lifecycle v1A-1 — Activity strip on JobDetail.
 *
 * Renders the audit trail returned by `GET /jobs/{id}/audit`,
 * newest-first, up to a soft cap of 50 rows (the backend currently
 * returns the full set; we cap client-side to keep the panel from
 * dominating long-running jobs).
 *
 * Each row renders:
 *   - localised timestamp (ISO → toLocaleString)
 *   - actor identity (UUID for v1A-1; future batch could resolve
 *     to email via a `useUser(actor_user_id)` hook)
 *   - one-line summary derived from `action` + `changed_fields`
 */
export function JobAuditTrail({ jobId }: { jobId: string }) {
  const { t } = useTranslation()
  const q = useJobAuditTrail(jobId)

  const rows: JobAuditRow[] = useMemo(() => {
    return (q.data ?? []).slice(0, 50)
  }, [q.data])

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-slate-700">
        {t('jobs.audit_trail_heading')}
      </h2>
      {q.isLoading && (
        <p className="text-sm text-slate-500">{t('common.loading')}</p>
      )}
      {q.isError && (
        <p className="text-sm text-red-600">
          {t('common.error')}: {extractErrorMessage(q.error)}
        </p>
      )}
      {q.data && rows.length === 0 && (
        <p className="text-sm text-slate-500 italic">
          {t('jobs.audit_trail_empty')}
        </p>
      )}
      {rows.length > 0 && (
        <ol className="space-y-1.5">
          {rows.map((row) => (
            <AuditRowLine key={row.audit_id} row={row} />
          ))}
        </ol>
      )}
    </section>
  )
}

function AuditRowLine({ row }: { row: JobAuditRow }) {
  const { t } = useTranslation()
  const summary = useMemo(() => deriveSummary(row, t), [row, t])
  const when = useMemo(() => {
    try {
      return new Date(row.created_at).toLocaleString()
    } catch {
      return row.created_at
    }
  }, [row.created_at])
  return (
    <li className="text-xs text-slate-700 border-l-2 border-slate-200 pl-3 py-1">
      <div className="text-slate-500 tabular-nums">{when}</div>
      <div className="text-slate-900">{summary}</div>
    </li>
  )
}

/** Build a one-line human-readable summary from a v1A-1 audit row. */
function deriveSummary(
  row: JobAuditRow,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  // Status transitions take precedence in display ordering.
  if (row.action === 'archive') {
    return t('jobs.audit_row_status_change', {
      old: t('jobs.status_active'),
      new: t('jobs.status_completed'),
    })
  }
  if (row.action === 'reopen') {
    return t('jobs.audit_row_status_change', {
      old: t('jobs.status_completed'),
      new: t('jobs.status_active'),
    })
  }
  // v1A-3 (future): delete action.
  if (row.action === 'delete') {
    return t('jobs.audit_row_deleted')
  }
  // "edit" action: render the most informative field diff inline.
  const cf = row.changed_fields ?? {}
  if ('job_name' in cf) {
    const diff = cf.job_name
    return t('jobs.audit_row_renamed', {
      old: String(diff.old ?? ''),
      new: String(diff.new ?? ''),
    })
  }
  // Multi-field or single-field edits other than rename — list the
  // changed field names.
  const fieldNames = Object.keys(cf)
  if (fieldNames.length === 1) {
    return t('jobs.audit_row_field_change', { field: fieldNames[0] })
  }
  return t('jobs.audit_row_multi_field_change', {
    fields: fieldNames.join(', '),
  })
}
