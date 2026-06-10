import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type SupplierPublic = components['schemas']['SupplierPublic'];

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
