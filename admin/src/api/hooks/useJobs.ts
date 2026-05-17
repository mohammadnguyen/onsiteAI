import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type JobPublic = components['schemas']['JobPublic']
type JobCreate = components['schemas']['JobCreate']
type JobUpdate = components['schemas']['JobUpdate']
type JobWithDetailPublic = components['schemas']['JobWithDetailPublic']
type JobAliasCreate = components['schemas']['JobAliasCreate']
type JobAliasPublic = components['schemas']['JobAliasPublic']
type JobCategoryBudgetCreate = components['schemas']['JobCategoryBudgetCreate']
type JobCategoryBudgetPublic = components['schemas']['JobCategoryBudgetPublic']
type CategoryPublic = components['schemas']['CategoryPublic']

// Job Lifecycle v1A-1: hand-written type for the new audit endpoint.
// Kept inline (rather than regenerating openapi types) to keep the
// change surface for this batch small. When the openapi types are
// next regenerated, this can be replaced with
// `components['schemas']['JobAuditRow']` and the export removed.
export type JobAuditRow = {
  audit_id: string
  tenant_id: string
  job_id: string | null
  job_name_snapshot: string
  job_code_snapshot: string | null
  actor_user_id: string
  action: 'edit' | 'archive' | 'reopen' | 'delete' | string
  changed_fields: Record<string, { old: unknown; new: unknown }>
  created_at: string
}

export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: async (): Promise<JobPublic[]> => {
      const { data } = await api.get<JobPublic[]>('/jobs')
      return data
    },
  })
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ['jobs', jobId],
    enabled: !!jobId,
    queryFn: async (): Promise<JobWithDetailPublic> => {
      const { data } = await api.get<JobWithDetailPublic>(`/jobs/${jobId}`)
      return data
    },
  })
}

export function useCreateJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: JobCreate): Promise<JobPublic> => {
      const { data } = await api.post<JobPublic>('/jobs', body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/**
 * Phase 3 Lite+ — PATCH /jobs/{id}.
 *
 * Used by the new Job Settings form (Batch 2) so the user can edit
 * contract value, total budget, target profit %, and warning
 * thresholds in place. Invalidates both the list and the single-job
 * cache so any embedded `summary` or `budget-summary` consumer picks
 * up the new values without a manual refetch.
 */
export function useUpdateJob(jobId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: JobUpdate): Promise<JobPublic> => {
      const { data } = await api.patch<JobPublic>(`/jobs/${jobId}`, body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      if (jobId) {
        void qc.invalidateQueries({ queryKey: ['jobs', jobId] })
        void qc.invalidateQueries({ queryKey: ['jobs', jobId, 'budget-summary'] })
        // v1A-1: refresh the audit-trail strip after any PATCH so the
        // user sees the new row land without a manual refresh.
        void qc.invalidateQueries({ queryKey: ['jobs', jobId, 'audit'] })
      }
    },
  })
}

/**
 * Job Lifecycle v1A-1: GET /jobs/{id}/audit.
 *
 * Returns the audit trail for a single job (admin only). Used by the
 * Activity strip on JobDetail. Cache is invalidated by `useUpdateJob`
 * so any successful PATCH refreshes the visible audit list.
 */
export function useJobAuditTrail(jobId: string | undefined) {
  return useQuery({
    queryKey: ['jobs', jobId, 'audit'],
    enabled: !!jobId,
    queryFn: async (): Promise<JobAuditRow[]> => {
      const { data } = await api.get<JobAuditRow[]>(`/jobs/${jobId}/audit`)
      return data
    },
  })
}

/**
 * Job Lifecycle v1A-3: DELETE /jobs/{id}.
 *
 * Hard-deletes an EMPTY job (zero expenses + zero queue rows). The
 * backend pre-checks dependencies and returns 409 with a friendly
 * "Archive it instead" detail when blocked; the consuming component
 * renders the detail string verbatim in the confirm dialog.
 *
 * On success (204), invalidates the jobs list cache. The caller is
 * responsible for navigating away from JobDetail (the page no
 * longer has a target after delete).
 */
export function useDeleteEmptyJob(jobId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (
      params: { reason?: string } = {},
    ): Promise<void> => {
      await api.delete(`/jobs/${jobId}`, {
        params: params.reason ? { reason: params.reason } : undefined,
      })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useAddAlias(jobId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: JobAliasCreate): Promise<JobAliasPublic> => {
      const { data } = await api.post<JobAliasPublic>(`/jobs/${jobId}/aliases`, body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
  })
}

export function useAddCategoryBudget(jobId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: JobCategoryBudgetCreate): Promise<JobCategoryBudgetPublic> => {
      const { data } = await api.post<JobCategoryBudgetPublic>(
        `/jobs/${jobId}/category-budgets`,
        body,
      )
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
  })
}

export function useCategories() {
  return useQuery({
    queryKey: ['categories'],
    queryFn: async (): Promise<CategoryPublic[]> => {
      const { data } = await api.get<CategoryPublic[]>('/categories')
      return data
    },
  })
}
