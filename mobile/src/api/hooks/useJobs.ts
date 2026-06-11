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
export type JobCategoryBudgetPublic = components['schemas']['JobCategoryBudgetPublic'];
export type JobCategoryBudgetCreateInput = components['schemas']['JobCategoryBudgetCreate'];

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

/**
 * Slice B mobile — POST /jobs/{job_id}/category-budgets (admin-only).
 *
 * Adds a new per-category budget row. 409 from the backend when
 * (job_id, category_id) already has a budget; the caller (mobile edit
 * screen) prevents this duplicate by filtering already-budgeted
 * categories out of the add-row picker, so 409 should only surface in
 * a race with another admin. Invalidates `['jobs']` so the per-job
 * detail (which carries `category_budgets` inline) AND the per-job
 * budget summary both refetch.
 */
export function useCreateJobCategoryBudget(jobId: string) {
  const qc = useQueryClient();
  return useMutation<
    JobCategoryBudgetPublic,
    unknown,
    { category_id: string; budget_amount_ex_gst: number | string }
  >({
    mutationFn: async (body) => {
      const { data } = await api.post<JobCategoryBudgetPublic>(
        `/jobs/${jobId}/category-budgets`,
        body,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Slice B mobile — PATCH /jobs/{job_id}/category-budgets/{budget_id}
 * (admin-only; Slice A backend endpoint shipped at commit 689ec15).
 *
 * Updates only the amount of an existing budget row. The Pydantic
 * `JobCategoryBudgetUpdate` schema is not yet in the generated
 * `mobile/src/api/types.ts` (drift acknowledged in CLAUDE.md); the
 * body shape `{ budget_amount_ex_gst }` is defined inline here. The
 * atomic (job_id, budget_id) pair check happens server-side — 404
 * with detail "Budget not found" if the pair doesn't resolve (also
 * the response when the budget belongs to a different job, by
 * design — no information leak).
 */
export function useUpdateJobCategoryBudget(jobId: string) {
  const qc = useQueryClient();
  return useMutation<
    JobCategoryBudgetPublic,
    unknown,
    { budgetId: string; budget_amount_ex_gst: number | string }
  >({
    mutationFn: async ({ budgetId, budget_amount_ex_gst }) => {
      const { data } = await api.patch<JobCategoryBudgetPublic>(
        `/jobs/${jobId}/category-budgets/${budgetId}`,
        { budget_amount_ex_gst },
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Slice B mobile — DELETE /jobs/{job_id}/category-budgets/{budget_id}
 * (admin-only; Slice A backend endpoint shipped at commit 689ec15).
 *
 * Non-idempotent on the backend by design: a second DELETE on the
 * same `budgetId` returns 404, NOT a silent 204. The caller
 * (mobile edit screen) confirms via `Alert.alert` before invoking
 * so accidental double-taps are avoided at the UX layer.
 */
export function useDeleteJobCategoryBudget(jobId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, { budgetId: string }>({
    mutationFn: async ({ budgetId }) => {
      await api.delete(`/jobs/${jobId}/category-budgets/${budgetId}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * M5 — DELETE /jobs/{id} (admin-only; backend Job Lifecycle v1A-3).
 *
 * Hard-deletes an EMPTY job only. The server is the guard: any
 * expense or review-queue row on the job produces a 409 whose detail
 * ("Job has N expenses and cannot be deleted. Archive it instead.")
 * the caller surfaces verbatim. Aliases + category budgets cascade
 * server-side; the job's audit row survives the delete.
 *
 * Takes {jobId} per call (mirrors useUpdateUser's shape) so the job
 * detail modal can act without per-row hook instantiation.
 */
export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation<void, unknown, { jobId: string }>({
    mutationFn: async ({ jobId }) => {
      await api.delete(`/jobs/${jobId}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}
