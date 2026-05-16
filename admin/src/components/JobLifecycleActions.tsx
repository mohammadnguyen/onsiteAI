import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useUpdateJob } from '../api/hooks/useJobs'
import { extractErrorMessage } from '../api/client'
import { Modal } from './Modal'
import type { components } from '../api/types'

type JobWithDetailPublic = components['schemas']['JobWithDetailPublic']

/**
 * Job Lifecycle v1A-2 — Archive / Reopen UI.
 *
 * Renders the lifecycle status badge plus an Archive (when active) or
 * Reopen (when completed) button. The button opens a confirm dialog;
 * confirming sends a PATCH /jobs/{id} with the new ``status`` value
 * via the existing ``useUpdateJob`` hook. No new backend code: v1A-1
 * already wired the ``status`` audit-row write (action="archive"
 * when status flips to completed, "reopen" when it flips back to
 * active) and the parser already excludes completed jobs from
 * matching (parser/jobs.py:211).
 *
 * No ``reason`` input field in this batch (R1 = Option B from the
 * v1A-2 plan): the audit table has no ``reason`` column, so showing
 * a UI input that doesn't persist would be dishonest.
 */
export function JobLifecycleActions({ job }: { job: JobWithDetailPublic }) {
  const { t } = useTranslation()
  const updateJob = useUpdateJob(job.job_id)

  const isCompleted = job.status === 'completed'
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setError(null)
    try {
      await updateJob.mutateAsync({
        status: isCompleted ? 'active' : 'completed',
      })
      setConfirmOpen(false)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  return (
    <div className="flex items-center gap-3">
      {/* Status badge — distinct visual for archived/completed state. */}
      <span
        className={
          isCompleted
            ? 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-200 text-slate-700 italic'
            : 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800'
        }
      >
        {isCompleted ? t('jobs.status_completed') : t('jobs.status_active')}
      </span>

      <button
        type="button"
        onClick={() => {
          setError(null)
          setConfirmOpen(true)
        }}
        className={
          isCompleted
            ? 'bg-slate-100 text-slate-800 rounded-md px-3 py-1.5 text-sm font-medium hover:bg-slate-200'
            : 'bg-amber-100 text-amber-900 rounded-md px-3 py-1.5 text-sm font-medium hover:bg-amber-200'
        }
      >
        {isCompleted ? t('jobs.reopen') : t('jobs.archive')}
      </button>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={
          isCompleted
            ? t('jobs.reopen_confirm_title')
            : t('jobs.archive_confirm_title')
        }
      >
        <p className="text-sm text-slate-700 mb-4">
          {isCompleted
            ? t('jobs.reopen_confirm_body')
            : t('jobs.archive_confirm_body')}
        </p>
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2 mb-3">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setConfirmOpen(false)}
            className="bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200"
            disabled={updateJob.isPending}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            disabled={updateJob.isPending}
          >
            {updateJob.isPending
              ? t('common.loading')
              : isCompleted
                ? t('jobs.reopen')
                : t('jobs.archive')}
          </button>
        </div>
      </Modal>
    </div>
  )
}
