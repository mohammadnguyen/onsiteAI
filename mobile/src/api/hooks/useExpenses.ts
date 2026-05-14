import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type ExpenseCreateInput = components['schemas']['ExpenseCreate-Input'];
export type ExpenseCreateResponse = components['schemas']['ExpenseCreateResponse'];
export type ExpenseListResponse = components['schemas']['ExpenseListResponse'];
export type ExpensePublic = components['schemas']['ExpensePublic'];
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
