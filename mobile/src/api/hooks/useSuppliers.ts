import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type SupplierPublic = components['schemas']['SupplierPublic'];
export type SupplierAliasPublic = components['schemas']['SupplierAliasPublic'];
export type LanguageCode = components['schemas']['LanguageCode'];

/**
 * M2-B — GET /suppliers.
 *
 * Lists suppliers for the full-expenses-list filter picker. Any
 * authenticated caller may read (admin + contributor see the same
 * list — supplier names are tenant-wide reference data, not
 * per-user). Mirrors `useCategories` exactly: global cache key,
 * no job scoping, read-only (mobile never creates or edits
 * suppliers; that stays on admin web).
 *
 * Cached under `['suppliers']`; nothing on mobile mutates suppliers,
 * so no invalidation wiring is needed.
 */
export function useSuppliers() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<SupplierPublic[]>({
    queryKey: ['suppliers'],
    queryFn: async () => {
      const r = await api.get<SupplierPublic[]>('/suppliers');
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}

/**
 * A3 — POST /suppliers (admin-only on the backend; contributors 403).
 * Used by the review corrections sheet's supplier quick-create so an
 * unknown supplier can be created inline while resolving a review item.
 * Invalidates ['suppliers'] so the new row appears in pickers.
 */
export function useCreateSupplier() {
  const qc = useQueryClient();
  return useMutation<SupplierPublic, unknown, { supplier_name: string }>({
    mutationFn: async ({ supplier_name }) => {
      const { data } = await api.post<SupplierPublic>('/suppliers', {
        supplier_name,
        is_active: true,
      });
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['suppliers'] });
    },
  });
}

/**
 * A3 — POST /suppliers/{id}/aliases (admin-only). Teaches the parser the
 * supplier's name/spelling so future captures of the same shop stop
 * re-entering the review queue. Best-effort companion to quick-create.
 */
export function useAddSupplierAlias() {
  return useMutation<
    SupplierAliasPublic,
    unknown,
    { supplierId: string; alias_text: string; language_code: LanguageCode }
  >({
    mutationFn: async ({ supplierId, alias_text, language_code }) => {
      const { data } = await api.post<SupplierAliasPublic>(
        `/suppliers/${supplierId}/aliases`,
        { alias_text, language_code },
      );
      return data;
    },
  });
}
