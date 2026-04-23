import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type ExpensePublic = components['schemas']['ExpensePublic']
type ExpenseDetailPublic = components['schemas']['ExpenseDetailPublic']
type ExpenseListResponse = components['schemas']['ExpenseListResponse']
type ExpenseCreateInput = components['schemas']['ExpenseCreate-Input']
type ExpenseCreateResponse = components['schemas']['ExpenseCreateResponse']
type ExpenseUpdate = components['schemas']['ExpenseUpdate']
type AuditRow = components['schemas']['AuditRow']
type ReviewStatus = components['schemas']['ReviewStatus']
type ReceiptStatus = components['schemas']['ReceiptStatus']

export type ExpenseFilters = {
  job_id?: string | null
  status?: ReviewStatus | null
  mine?: 0 | 1
  from?: string | null
  to?: string | null
  receipt_status?: ReceiptStatus | null
  limit?: number
  cursor?: string | null
}

function pruneFilters(
  filters: ExpenseFilters | undefined,
): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  if (!filters) return out
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === '') continue
    out[k] = v as string | number
  }
  return out
}

export function useExpenses(filters?: ExpenseFilters) {
  return useQuery({
    queryKey: ['expenses', filters ?? {}],
    queryFn: async (): Promise<ExpenseListResponse> => {
      const { data } = await api.get<ExpenseListResponse>('/expenses', {
        params: pruneFilters(filters),
      })
      return data
    },
  })
}

export function useExpense(expenseId: string | undefined) {
  return useQuery({
    queryKey: ['expenses', expenseId],
    enabled: !!expenseId,
    queryFn: async (): Promise<ExpenseDetailPublic> => {
      const { data } = await api.get<ExpenseDetailPublic>(`/expenses/${expenseId}`)
      return data
    },
  })
}

export function useExpenseAudit(expenseId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['expenses', expenseId, 'audit'],
    enabled: !!expenseId && enabled,
    queryFn: async (): Promise<AuditRow[]> => {
      const { data } = await api.get<AuditRow[]>(`/expenses/${expenseId}/audit`)
      return data
    },
  })
}

export function useCreateExpense() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ExpenseCreateInput): Promise<ExpenseCreateResponse> => {
      const { data } = await api.post<ExpenseCreateResponse>('/expenses', body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

export function useUpdateExpense() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      expenseId,
      body,
    }: {
      expenseId: string
      body: ExpenseUpdate
    }): Promise<ExpensePublic> => {
      const { data } = await api.patch<ExpensePublic>(`/expenses/${expenseId}`, body)
      return data
    },
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['expenses'] })
      void qc.invalidateQueries({ queryKey: ['expenses', vars.expenseId] })
      void qc.invalidateQueries({ queryKey: ['expenses', vars.expenseId, 'audit'] })
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })
}

export function useDeleteExpense() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      expenseId,
      reason,
    }: {
      expenseId: string
      reason?: string | null
    }): Promise<void> => {
      await api.delete(`/expenses/${expenseId}`, {
        params: reason ? { reason } : undefined,
      })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['expenses'] })
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })
}
