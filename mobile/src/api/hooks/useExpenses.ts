import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import type { components } from '../types';

export type ExpenseCreateInput = components['schemas']['ExpenseCreate-Input'];
export type ExpenseCreateResponse = components['schemas']['ExpenseCreateResponse'];
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
