import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';

/**
 * Org settings (admin-only, both verbs on the backend).
 *
 * ``default_day_hours``: how many hours a labour "day" is worth when
 * an entry records attendance without hours (founder 2026-08-24).
 * Decimal-string in transit like every quantity — the client formats,
 * never computes. Local type (not the generated OpenAPI map): one
 * field, admin-only surface.
 */
export type OrgSettings = { default_day_hours: string };

export function useOrgSettings(enabled: boolean) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<OrgSettings>({
    queryKey: ['org-settings'],
    queryFn: async () => {
      const r = await api.get<OrgSettings>('/org-settings');
      return r.data;
    },
    // Caller passes the isAdmin gate — contributors never fire a
    // guaranteed-403 request (C-09 posture).
    enabled: !!accessToken && enabled,
    retry: false,
  });
}

export function useUpdateOrgSettings() {
  const qc = useQueryClient();
  return useMutation<OrgSettings, unknown, { default_day_hours: string }>({
    mutationFn: async (body) => {
      const r = await api.patch<OrgSettings>('/org-settings', body);
      return r.data;
    },
    onSuccess: (data) => {
      qc.setQueryData(['org-settings'], data);
      // The parameter re-prices hours-less entries at read time, so
      // every cost view is stale the moment it changes.
      void qc.invalidateQueries({ queryKey: ['labour-summary'] });
      void qc.invalidateQueries({ queryKey: ['labour-rollup'] });
    },
  });
}
