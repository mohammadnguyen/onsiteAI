import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';
import type { ExpenseListResponse } from './useExpenses';

export type ReviewQueueItem = components['schemas']['ReviewQueuePublic'];

/**
 * M3: open review-queue rows for the mobile triage screen.
 *
 * Backend: GET /review-queue?status=open
 *   - admin-only (require_admin); contributors get 403 — the screen
 *     maps that to the existing "admins only" message. UI-side role
 *     gating is visibility only; the backend stays authoritative.
 *   - rows ordered opened_at ASC (oldest waiting first) — the right
 *     triage order, so the client does NOT re-sort.
 *   - row shape (ReviewQueuePublic) carries ids, review_reasons,
 *     status and timestamps but NO expense summary — by design M3 v1
 *     joins client-side against the pending-expenses list (operator
 *     decision: Option 1; backend enrichment deferred unless dogfood
 *     proves the join inadequate).
 *
 * Cache root: ['review-queue'] — every mutation that can change the
 * queue (capture, resolve, reject, delete, edit) invalidates this
 * root in useExpenses.ts, so the triage list refreshes after any
 * action without extra wiring.
 */
export function useOpenReviewQueue() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<ReviewQueueItem[]>({
    queryKey: ['review-queue', 'open'],
    queryFn: async () => {
      const r = await api.get<ReviewQueueItem[]>('/review-queue', {
        params: { status: 'open' },
      });
      return r.data;
    },
    enabled: !!accessToken,
    staleTime: 0,
    retry: false,
  });
}

/**
 * M3: pending-expense summaries for the Option 1 client-side join.
 *
 * GET /expenses?status=pending&limit=500 (the backend cap). Every
 * pending expense has exactly ONE open queue row (DB-enforced 1:1),
 * so joining queue rows to this page by expense_id is total for any
 * realistic triage volume. If open rows ever exceeded 500, the extra
 * rows render DEGRADED (reasons + waiting-since, still tappable) —
 * an accepted limitation treated as a product alarm, not a UI bug.
 *
 * Keyed UNDER the ['expenses'] root so the existing
 * create/update/delete/resolve/reject invalidations refresh the
 * summaries automatically.
 */
export function usePendingExpenseSummaries() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<ExpenseListResponse>({
    queryKey: ['expenses', { pendingSummaries: true }],
    queryFn: async () => {
      const { data } = await api.get<ExpenseListResponse>('/expenses', {
        params: { status: 'pending', limit: 500 },
      });
      return data;
    },
    enabled: !!accessToken,
    staleTime: 0,
    retry: false,
  });
}
