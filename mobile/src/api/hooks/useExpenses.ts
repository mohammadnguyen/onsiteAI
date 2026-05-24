import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type ExpenseCreateInput = components['schemas']['ExpenseCreate-Input'];
export type ExpenseCreateResponse = components['schemas']['ExpenseCreateResponse'];
export type ExpenseListResponse = components['schemas']['ExpenseListResponse'];
export type ExpensePublic = components['schemas']['ExpensePublic'];
export type ExpenseDetailPublic = components['schemas']['ExpenseDetailPublic'];
export type ExpenseUpdateInput = components['schemas']['ExpenseUpdate'];
export type ParseDiagnostics = components['schemas']['ParseDiagnostics'];
export type ReviewReasonCode = components['schemas']['ReviewReasonCode'];
export type ReviewStatus = components['schemas']['ReviewStatus'];
export type PaymentMethod = components['schemas']['PaymentMethod'];
export type ReceiptStatus = components['schemas']['ReceiptStatus'];

/**
 * Mobile Capture v0: minimal POST /expenses mutation.
 *
 * Mirrors `admin/src/api/hooks/useExpenses.ts:useCreateExpense` so the
 * two clients use the same wire shape + the same query-key
 * invalidation convention.
 *
 * Body construction is the caller's responsibility — the capture
 * screen uses conditional spreads to avoid sending explicit `null` for
 * unset Pydantic fields (the `model_fields_set` 422 trap; see
 * `admin/src/pages/Capture.tsx` for the documented workaround).
 */
export function useCreateExpense() {
  const qc = useQueryClient();
  return useMutation<ExpenseCreateResponse, unknown, ExpenseCreateInput>({
    mutationFn: async (body) => {
      const { data } = await api.post<ExpenseCreateResponse>('/expenses', body);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
    },
  });
}

/**
 * Mobile Capture v1 Sub-batch A: read-only "My Captures" list.
 *
 * Always passes `mine=1` so the result is user-scoped for admins and
 * contributors alike (the backend service auto-scopes contributors;
 * the explicit `mine=1` keeps admins from seeing the whole tenant on
 * the phone). Default `limit=20`; the backend caps at 500 anyway.
 *
 * Server already orders newest first
 * (`expense_date DESC, created_at DESC`), so no client-side sorting
 * is required.
 *
 * Shares the `['expenses', ...]` queryKey prefix with
 * `useCreateExpense`'s invalidator so a successful capture refetches
 * this query without additional wiring.
 */
export function useMyRecentExpenses(limit: number = 20) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<ExpenseListResponse>({
    queryKey: ['expenses', { mine: 1, limit }],
    queryFn: async () => {
      const { data } = await api.get<ExpenseListResponse>('/expenses', {
        params: { mine: 1, limit },
      });
      return data;
    },
    enabled: !!accessToken,
    staleTime: 0,
    retry: false,
  });
}

/**
 * Per-job expenses list for the mobile job detail modal.
 *
 * Reuses GET /expenses?job_id=X&limit=N. Backend already supports
 * the job_id filter (no new endpoint needed). Returns the same
 * ExpenseListResponse shape as useMyRecentExpenses, so the same
 * RecentCapturesList component can render either source.
 *
 * Note: this query does NOT pass mine=1 — admin viewing a job sees
 * ALL expenses on that job (not just their own captures), which is
 * the right semantic for correction-driven workflows.
 */
export function useJobExpenses(jobId: string | null, limit: number = 20) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<ExpenseListResponse>({
    queryKey: ['expenses', { job_id: jobId, limit }],
    queryFn: async () => {
      const { data } = await api.get<ExpenseListResponse>('/expenses', {
        params: { job_id: jobId, limit },
      });
      return data;
    },
    enabled: !!accessToken && !!jobId,
    staleTime: 0,
    retry: false,
  });
}

/**
 * Mobile Expense Detail (v1): single-expense fetch for the read-only
 * detail screen at `app/expenses/[id].tsx`.
 *
 * Mirrors the admin hook shape (`admin/src/api/hooks/useExpenses.ts:useExpense`).
 * Returns `ExpenseDetailPublic`, which now carries an optional
 * `review_reasons` array (the consumer uses `?? []`).
 *
 * The 404 path (deleted-out-from-under-you / unknown id) is surfaced
 * via `query.isError` + the axios error; the detail screen renders a
 * dedicated NotFound state. The 403 path (contributor reading
 * someone else's expense — defence in depth; should not happen for
 * rows that surfaced in the user's own captures list) is treated
 * identically to 404 at the UI layer.
 */
export function useExpense(expenseId: string | undefined) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<ExpenseDetailPublic>({
    queryKey: ['expenses', expenseId],
    queryFn: async () => {
      const { data } = await api.get<ExpenseDetailPublic>(`/expenses/${expenseId}`);
      return data;
    },
    enabled: !!accessToken && !!expenseId,
    staleTime: 0,
    retry: false,
  });
}

/**
 * Resolve (approve) a pending review-queue item.
 *
 * Backend: POST /review-queue/{review_id}/resolve
 *   - admin-only (require_admin); 403 for contributors
 *   - returns 204 on success
 *   - flips queue row to `resolved` + expense.review_status to `reviewed`
 *   - writes audit log
 *
 * Mobile usage: gated on `ExpenseDetailPublic.pending_review_queue_id`
 * presence — never on `review_status` alone, so a historical
 * resolved/rejected queue row can't leak as a callable action.
 *
 * Body: empty object in v1 (operator brief: no reason input).
 * Backend's `ResolveRequest` accepts optional `notes` and
 * `expense_patch`; we send neither.
 *
 * Cache invalidation: `['expenses']` AND `['jobs']` roots (mirrors
 * useDeleteExpense; covers My Captures, expense detail, per-job
 * expense list, per-job budget summary, jobs list).
 */
export function useResolveQueueItem(reviewId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, void>({
    mutationFn: async () => {
      await api.post(`/review-queue/${reviewId}/resolve`, {});
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Reject a pending review-queue item.
 *
 * Backend: POST /review-queue/{review_id}/reject
 *   - admin-only; 403 for contributors
 *   - returns 204 on success
 *   - flips queue row to `rejected` + expense.review_status to `rejected`
 *   - writes audit log
 *
 * Same gating + cache-invalidation pattern as useResolveQueueItem.
 * Body: empty (notes deferred to a future slice if dogfood warrants).
 */
export function useRejectQueueItem(reviewId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, void>({
    mutationFn: async () => {
      await api.post(`/review-queue/${reviewId}/reject`, {});
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * Soft-delete mutation for the mobile expense detail screen.
 *
 * Backend: DELETE /expenses/{id}
 *   - admin-only (require_admin); contributors get 403
 *   - returns 204 on success
 *   - semantics: sets review_status='rejected' + writes audit log
 *     (the row stays in the DB; it's removed from active lists)
 *
 * Cache invalidation: BOTH `['expenses']` and `['jobs']` roots,
 * because a delete affects (a) every expense list query — My
 * Captures, per-job expense list, expense detail — and (b) the
 * job's budget summary totals when the deleted row had a job_id.
 * The job query keys are `['jobs', jobId, 'budget-summary']` and
 * `['jobs', jobId]`; invalidating the `['jobs']` prefix catches
 * both transitively.
 */
export function useDeleteExpense(expenseId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, void>({
    mutationFn: async () => {
      await api.delete(`/expenses/${expenseId}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * P4: PATCH /expenses/{id} mutation for the mobile edit screen.
 *
 * Mirrors the admin `useUpdateExpense` shape (admin/src/api/hooks/useExpenses.ts).
 * Sends only the caller-supplied fields — the edit screen builds a
 * conditional-spread body so unchanged fields are omitted entirely
 * (NOT sent as null), avoiding the Pydantic `model_fields_set` 422 trap
 * that would clobber existing values on the backend.
 *
 * Backend role semantics (handled server-side, not in this hook):
 *   - contributor: edit own row while review_status='pending'
 *   - admin:       any row; edits on reviewed rows require `reason`
 *                  (mobile UI does NOT surface `reason` in P4 — admin
 *                   reviewed-row edits stay on admin web until P4.5)
 *   - job_id is IMMUTABLE post-create (any attempt → 422)
 *
 * Cache invalidation: hits the `['expenses']` root so both the detail
 * cache (`['expenses', id]`) and the My-Captures list cache
 * (`['expenses', { mine: 1, limit }]`) refetch after a successful PATCH.
 */
export function useUpdateExpense(expenseId: string) {
  const qc = useQueryClient();
  return useMutation<ExpensePublic, unknown, ExpenseUpdateInput>({
    mutationFn: async (body) => {
      const { data } = await api.patch<ExpensePublic>(`/expenses/${expenseId}`, body);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
    },
  });
}
