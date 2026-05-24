import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type JobPublic = components['schemas']['JobPublic'];
export type JobWithDetailPublic = components['schemas']['JobWithDetailPublic'];
export type JobCreateInput = components['schemas']['JobCreate'];
export type JobUpdateInput = components['schemas']['JobUpdate'];
export type JobAliasCreateInput = components['schemas']['JobAliasCreate'];
export type JobAliasPublic = components['schemas']['JobAliasPublic'];
export type JobBudgetSummary = components['schemas']['JobBudgetSummary'];
export type CategoryBudgetRow = components['schemas']['CategoryBudgetRow'];
export type JobStatus = components['schemas']['JobStatus'];

export function useJobs() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<JobPublic[]>({
    queryKey: ['jobs'],
    queryFn: async () => {
      const r = await api.get<JobPublic[]>('/jobs');
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}

export function useJob(jobId: string | null) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<JobWithDetailPublic>({
    queryKey: ['jobs', jobId],
    queryFn: async () => {
      const r = await api.get<JobWithDetailPublic>(`/jobs/${jobId}`);
      return r.data;
    },
    enabled: !!accessToken && !!jobId,
    retry: false,
  });
}

/**
 * Per-job spend + budget summary for the mobile job detail modal.
 *
 * Endpoint is admin-only on the backend. Contributors will receive 403,
 * which the caller (jobs.tsx detail modal) detects and uses to HIDE the
 * spending section silently — no error banner for an expected
 * permission shape. Other errors (network, 500) are surfaced as a
 * non-blocking "couldn't load spending" message so dogfooding doesn't
 * lose the failure signal.
 *
 * Query is parallel to ``useJob``: both fire when the modal opens, so
 * the spending data is often ready by the time the user has scanned
 * the identity rows.
 */
export function useJobBudgetSummary(jobId: string | null) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<JobBudgetSummary>({
    queryKey: ['jobs', jobId, 'budget-summary'],
    queryFn: async () => {
      const r = await api.get<JobBudgetSummary>(
        `/jobs/${jobId}/budget-summary`,
      );
      return r.data;
    },
    enabled: !!accessToken && !!jobId,
    retry: false,
  });
}

/**
 * Tier 1B: PATCH /jobs/{id} mutation for the mobile job edit screen.
 *
 * Admin-only on the backend; contributors get 403. Sends only the
 * caller-supplied fields (conditional-spread body builder lives in
 * the edit screen). Numeric blanks must be sent as explicit null to
 * clear the value — NEVER as 0 (operator guardrail; 0 is a real
 * value that means "the contract is worth zero", which is a
 * different intent from "no contract value set").
 *
 * Cache invalidation: `['jobs']` root → covers the jobs list, the
 * job detail (used by the modal), the per-job budget summary, AND
 * any per-job queries derived from the jobs root prefix. Mirrors
 * the broad-by-design pattern used in useDeleteExpense /
 * useUpdateExpense — favour correctness over micro-optimisation.
 */
export function useUpdateJob(jobId: string) {
  const qc = useQueryClient();
  return useMutation<JobPublic, unknown, JobUpdateInput>({
    mutationFn: async (body) => {
      const { data } = await api.patch<JobPublic>(`/jobs/${jobId}`, body);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Mobile Job Management Lite — POST /jobs (admin-only on the backend).
 *
 * Mirrors `admin/src/api/hooks/useJobs.ts:useCreateJob` so the two
 * clients use the same wire shape + the same query-key invalidation.
 *
 * The mobile caller (`NewJobModal`) uses a conditional-spread body
 * builder so optional fields the user did not fill are omitted rather
 * than sent as explicit `null` (avoiding the Pydantic
 * `model_fields_set` 422 trap, same convention as capture v0).
 */
export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation<JobPublic, unknown, JobCreateInput>({
    mutationFn: async (body) => {
      const { data } = await api.post<JobPublic>('/jobs', body);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Mobile Job Management Lite — POST /jobs/{job_id}/aliases (admin-only).
 *
 * Different shape from admin's `useAddAlias(jobId)` (closure-style):
 * mobile creates the job and aliases in a single user gesture, so the
 * hook accepts `jobId` per call rather than per hook instantiation.
 * Each alias is submitted independently; the caller is responsible for
 * looping and aggregating partial-failure outcomes (a duplicate
 * normalised alias surfaces as HTTP 409 from the backend).
 */
export function useCreateJobAlias() {
  const qc = useQueryClient();
  return useMutation<
    JobAliasPublic,
    unknown,
    { jobId: string; alias_text: string }
  >({
    mutationFn: async ({ jobId, alias_text }) => {
      const body: JobAliasCreateInput = { alias_text };
      const { data } = await api.post<JobAliasPublic>(
        `/jobs/${jobId}/aliases`,
        body,
      );
      return data;
    },
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
      void qc.invalidateQueries({ queryKey: ['jobs', vars.jobId] });
    },
  });
}
