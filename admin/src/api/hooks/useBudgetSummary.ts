/**
 * Phase 3 Lite — budget-summary hook.
 *
 * Wraps `GET /jobs/{job_id}/budget-summary` (admin-only) for the Job
 * Detail page. The list page (`/jobs`) reads its summary off the
 * existing `useJobs` query — `JobPublic` now carries an embedded
 * `summary` field generated from the same source as this endpoint, so
 * both surfaces stay consistent without a separate fetch on the list.
 *
 * Cache key namespacing: `['jobs', jobId, 'budget-summary']` so adding
 * a category budget or approving a queue item from elsewhere can
 * invalidate just this slice without nuking the broader job query.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type JobBudgetSummary = components['schemas']['JobBudgetSummary']

export function useJobBudgetSummary(jobId: string | undefined) {
  return useQuery({
    queryKey: ['jobs', jobId, 'budget-summary'],
    enabled: !!jobId,
    queryFn: async (): Promise<JobBudgetSummary> => {
      const { data } = await api.get<JobBudgetSummary>(
        `/jobs/${jobId}/budget-summary`,
      )
      return data
    },
  })
}
