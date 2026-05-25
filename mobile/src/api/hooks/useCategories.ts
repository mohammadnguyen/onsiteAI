import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type CategoryPublic = components['schemas']['CategoryPublic'];

/**
 * Mobile Slice B (Tier 1C) — GET /categories.
 *
 * Lists the active categories used for tagging expenses and budgeting
 * per job. Any authenticated caller may read (admin + contributor see
 * the same list); the backend's `include_inactive` toggle is admin-only
 * and not exposed here — the mobile category-budget editor only ever
 * shows active categories (archived rows can't be a sensible target
 * for a NEW budget).
 *
 * Cached under `['categories']` rather than under any job-scoped key
 * because the list is global. Tier-1C mutations on per-job category
 * budgets invalidate `['jobs']` (job detail carries `category_budgets`
 * inline) but never touch `['categories']` — adding/removing a per-job
 * budget does not change which categories exist.
 */
export function useCategories() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<CategoryPublic[]>({
    queryKey: ['categories'],
    queryFn: async () => {
      const r = await api.get<CategoryPublic[]>('/categories');
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}
