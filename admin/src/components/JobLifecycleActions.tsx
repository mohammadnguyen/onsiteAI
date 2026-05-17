import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useDeleteEmptyJob, useUpdateJob } from '../api/hooks/useJobs'
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
  const navigate = useNavigate()
  const updateJob = useUpdateJob(job.job_id)
  const deleteJob = useDeleteEmptyJob(job.job_id)

  const isCompleted = job.status === 'completed'
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Job Lifecycle v1A-3 — Delete Empty Job. Separate dialog state so
  // the archive/reopen dialog and the delete dialog never overlap.
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

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

  const submitDelete = async () => {
    setDeleteError(null)
    try {
      await deleteJob.mutateAsync({})
      // Success — close dialog and bounce back to the jobs list,
      // since this detail page has nothing to render anymore.
      setDeleteConfirmOpen(false)
      navigate('/jobs')
    } catch (err) {
      // Backend's 409 detail string is the user-facing message
      // ("Job has N expenses and cannot be deleted. Archive it
      // instead."). Render verbatim with a localised prefix.
      setDeleteError(extractErrorMessage(err))
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

      {/* Job Lifecycle v1A-3 — Delete (only if empty). Always
          rendered; backend pre-check returns 409 if not allowed and
          the dialog displays the friendly "Archive it instead"
          message verbatim. No reason input field (R1=B, same as
          archive/reopen). */}
      <button
        type="button"
        onClick={() => {
          setDeleteError(null)
          setDeleteConfirmOpen(true)
        }}
        className="bg-red-50 text-red-700 rounded-md px-3 py-1.5 text-sm font-medium hover:bg-red-100"
      >
        {t('jobs.delete_empty')}
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

      {/* v1A-3 — Delete confirm dialog. Separate Modal so it can be
          shown independently of the archive/reopen dialog. The error
          row renders the backend's 409 detail string verbatim with a
          localised prefix ("Cannot delete: Job has N expenses and
          cannot be deleted. Archive it instead."). */}
      <Modal
        open={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        title={t('jobs.delete_confirm_title')}
      >
        <p className="text-sm text-slate-700 mb-4">
          {t('jobs.delete_confirm_body')}
        </p>
        {deleteError && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-md p-2 mb-3">
            {t('jobs.delete_blocked_prefix')}
            {deleteError}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setDeleteConfirmOpen(false)}
            className="bg-slate-100 text-slate-700 rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-200"
            disabled={deleteJob.isPending}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={submitDelete}
            className="bg-red-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-red-700 disabled:opacity-50"
            disabled={deleteJob.isPending}
          >
            {deleteJob.isPending
              ? t('common.loading')
              : t('jobs.delete_empty')}
          </button>
        </div>
      </Modal>
    </div>
  )
}
