import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useUpdateJob } from '../api/hooks/useJobs'
import { extractErrorMessage } from '../api/client'
import type { components } from '../api/types'

type JobWithDetailPublic = components['schemas']['JobWithDetailPublic']
type JobUpdate = components['schemas']['JobUpdate']

/**
 * Job Lifecycle v1A-1 — Edit Job Details.
 *
 * Three editable scalar fields: `job_name`, `job_code`, `site_address`,
 * plus an optional reason text (currently not persisted — see note
 * below). Submits via `useUpdateJob`. Only changed fields are sent in
 * the PATCH body so the backend's no-op short-circuit correctly skips
 * the audit row when the user opens-and-saves the form without
 * touching any field.
 *
 * Duplicate `job_code` surfaces as a 409 from the backend (per the
 * v1A-1 pre-check in `services/jobs.update_job`); the error message
 * lands in the form-level error banner with a localised string.
 *
 * Note on `reason`: v1A-1's audit table does NOT carry a `reason`
 * column (intentionally — the user spec for v1A-1 listed only
 * snapshots / changed_fields / actor / created_at). If the team
 * decides a reason column adds value, it lands in a follow-up
 * migration; the form's reason input is wired up here as UI scaffold
 * but its value is not yet sent to the backend.
 */
export function JobDetailsForm({ job }: { job: JobWithDetailPublic }) {
  const { t } = useTranslation()
  const updateJob = useUpdateJob(job.job_id)

  const [jobName, setJobName] = useState<string>(job.job_name)
  const [jobCode, setJobCode] = useState<string>(job.job_code ?? '')
  const [siteAddress, setSiteAddress] = useState<string>(job.site_address ?? '')
  const [reason, setReason] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [savedFlash, setSavedFlash] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSavedFlash(false)

    // Build PATCH body containing only the fields the user actually
    // changed (compared to the current job state). Sending unchanged
    // values would still be a no-op for audit purposes thanks to the
    // service-layer short-circuit, but trimming the body is honest.
    const body: JobUpdate = {}
    if (jobName !== job.job_name) {
      body.job_name = jobName
    }
    if ((jobCode || null) !== (job.job_code ?? null)) {
      body.job_code = jobCode.trim() === '' ? null : jobCode
    }
    if ((siteAddress || null) !== (job.site_address ?? null)) {
      body.site_address = siteAddress.trim() === '' ? null : siteAddress
    }
    // Nothing actually changed → don't fire the request.
    if (Object.keys(body).length === 0) {
      setSavedFlash(true)
      return
    }

    try {
      await updateJob.mutateAsync(body)
      setSavedFlash(true)
      setReason('')
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-700">
        {t('jobs.edit_details_heading')}
      </h2>

      <Field label={t('jobs.field_name_label')} required>
        <input
          required
          value={jobName}
          onChange={(e) => setJobName(e.target.value)}
          className={inputClass}
          maxLength={255}
        />
      </Field>

      <Field label={t('jobs.field_code_label')}>
        <input
          value={jobCode}
          onChange={(e) => setJobCode(e.target.value)}
          className={inputClass}
          maxLength={64}
        />
      </Field>

      <Field label={t('jobs.field_address_label')}>
        <input
          value={siteAddress}
          onChange={(e) => setSiteAddress(e.target.value)}
          className={inputClass}
          maxLength={512}
        />
      </Field>

      <Field label={t('jobs.field_reason_label_optional')}>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className={inputClass}
          placeholder={t('jobs.field_reason_placeholder')}
        />
      </Field>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
          {error}
        </div>
      )}

      {savedFlash && (
        <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-md p-2">
          {t('jobs.saved')}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={updateJob.isPending}
          className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
        >
          {updateJob.isPending ? t('common.loading') : t('jobs.save_changes')}
        </button>
      </div>
    </form>
  )
}

const inputClass =
  'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500'

function Field({
  label,
  children,
  required,
}: {
  label: string
  children: React.ReactNode
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </span>
      {children}
    </label>
  )
}
