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
      }
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
