import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type SupplierPublic = components['schemas']['SupplierPublic']
type SupplierCreate = components['schemas']['SupplierCreate']
type SupplierUpdate = components['schemas']['SupplierUpdate']
type SupplierAliasCreate = components['schemas']['SupplierAliasCreate']
type SupplierAliasPublic = components['schemas']['SupplierAliasPublic']

export function useSuppliers(activeOnly = false) {
  return useQuery({
    queryKey: ['suppliers', { activeOnly }],
    queryFn: async (): Promise<SupplierPublic[]> => {
      const { data } = await api.get<SupplierPublic[]>('/suppliers', {
        params: activeOnly ? { active_only: true } : undefined,
      })
      return data
    },
  })
}

export function useCreateSupplier() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: SupplierCreate): Promise<SupplierPublic> => {
      const { data } = await api.post<SupplierPublic>('/suppliers', body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['suppliers'] })
    },
  })
}

export function useUpdateSupplier() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      supplierId,
      body,
    }: {
      supplierId: string
      body: SupplierUpdate
    }): Promise<SupplierPublic> => {
      const { data } = await api.patch<SupplierPublic>(`/suppliers/${supplierId}`, body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['suppliers'] })
    },
  })
}

export function useAddSupplierAlias() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      supplierId,
      body,
    }: {
      supplierId: string
      body: SupplierAliasCreate
    }): Promise<SupplierAliasPublic> => {
      const { data } = await api.post<SupplierAliasPublic>(
        `/suppliers/${supplierId}/aliases`,
        body,
      )
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['suppliers'] })
    },
  })
}
