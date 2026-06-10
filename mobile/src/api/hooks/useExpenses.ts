import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
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
      // M3: a new capture can open a review-queue row.
      void qc.invalidateQueries({ queryKey: ['review-queue'] });
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
      void qc.invalidateQueries({ queryKey: ['review-queue'] }); // M3 triage list
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
      void qc.invalidateQueries({ queryKey: ['review-queue'] }); // M3 triage list
    },
  });
}

/** M1: optional audit-reason payload for useDeleteExpense. */
export type DeleteExpenseInput = { reason?: string };

/**
 * Soft-delete mutation for the mobile expense detail screen.
 *
 * Backend: DELETE /expenses/{id}?reason=...
 *   - admin-only (require_admin); contributors get 403
 *   - returns 204 on success
 *   - semantics: sets review_status='rejected' + writes audit log
 *     (the row stays in the DB; it's removed from active lists)
 *   - `reason` is an OPTIONAL query param (max_length=500) recorded
 *     on the audit row only — the backend NEVER requires it. Omitting
 *     it deletes exactly as before M1.
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
  return useMutation<void, unknown, DeleteExpenseInput>({
    mutationFn: async (input) => {
      // Trim + cap to the backend's max_length=500. axios `params`
      // URL-encodes the value safely. An empty/whitespace-only reason
      // is treated as "no reason" and the param is omitted entirely.
      const reason = input.reason?.trim().slice(0, 500);
      await api.delete(`/expenses/${expenseId}`, {
        params: reason ? { reason } : undefined,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
      void qc.invalidateQueries({ queryKey: ['review-queue'] }); // M3 triage list
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
 *   - admin:       any row, any status. `ExpenseUpdate.reason` is
 *                  OPTIONAL audit metadata — the backend does NOT
 *                  reject reason-less admin edits (verified against
 *                  app/services/expenses.py: `reason` is discarded
 *                  from the patch set and written to the audit log
 *                  only). M1's edit screen surfaces it on non-pending
 *                  rows and includes it only when the admin types one.
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
      // M3: an edit can change what the triage row shows.
      void qc.invalidateQueries({ queryKey: ['review-queue'] });
    },
  });
}

/** M2-B: filter set for the full expenses list. All fields optional;
 *  omitted fields send no query param (backend defaults apply). */
export type ExpenseListFilters = {
  jobId?: string;
  status?: ReviewStatus;
  supplierId?: string;
  categoryId?: string;
  /** ISO date (YYYY-MM-DD), inclusive lower bound on expense_date. */
  from?: string;
  /** ISO date (YYYY-MM-DD), inclusive upper bound on expense_date. */
  to?: string;
};

const EXPENSE_LIST_PAGE_SIZE = 25;

/**
 * M2-B: infinite full expenses list backed by M2-A keyset pagination.
 *
 * Backend: GET /expenses?cursor=...
 *   - ``next_cursor`` is an OPAQUE token — this hook echoes it back
 *     verbatim as ``cursor`` for the next page and never parses it.
 *     A null ``next_cursor`` means last page (getNextPageParam
 *     returns null → ``hasNextPage`` false).
 *   - Role scoping is server-side: admins get the whole tenant,
 *     contributors get only their own rows. No ``mine`` param is
 *     sent — this list intentionally shows "everything I'm allowed
 *     to see".
 *   - Page size 25 keeps pagination real at field data volumes
 *     (the backend caps limit at 500).
 *
 * Cache key sits under the ``['expenses']`` ROOT deliberately:
 * every existing mutation (create / update / delete / resolve /
 * reject) already invalidates that root, so this list refetches
 * after any expense mutation with zero new wiring. Filters are part
 * of the key, so each filter combination caches independently.
 */
export function useExpensesList(filters: ExpenseListFilters) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useInfiniteQuery({
    queryKey: ['expenses', 'list', filters],
    queryFn: async ({ pageParam }): Promise<ExpenseListResponse> => {
      const { data } = await api.get<ExpenseListResponse>('/expenses', {
        params: {
          limit: EXPENSE_LIST_PAGE_SIZE,
          ...(filters.jobId ? { job_id: filters.jobId } : {}),
          ...(filters.status ? { status: filters.status } : {}),
          ...(filters.supplierId ? { supplier_id: filters.supplierId } : {}),
          ...(filters.categoryId ? { category_id: filters.categoryId } : {}),
          ...(filters.from ? { from: filters.from } : {}),
          ...(filters.to ? { to: filters.to } : {}),
          ...(pageParam ? { cursor: pageParam } : {}),
        },
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? null,
    enabled: !!accessToken,
    // 30s rather than 0: the list screen remounts on every detail
    // round trip (root Slot navigator unmounts siblings), and
    // staleTime 0 would re-fire a sequential refetch of EVERY cached
    // page each time — an N-request chain on a field network.
    // Mutation freshness is unaffected: invalidateQueries(['expenses'])
    // marks the data stale and refetches regardless of staleTime.
    staleTime: 30_000,
    retry: false,
  });
}
