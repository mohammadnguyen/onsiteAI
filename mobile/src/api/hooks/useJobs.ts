import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type JobPublic = components['schemas']['JobPublic'];
export type JobWithDetailPublic = components['schemas']['JobWithDetailPublic'];

export function useJobs() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<JobPublic[]>({
    queryKey: ['jobs'],
    queryFn: async () => {
      const r = await api.get<JobPublic[]>('/jobs');
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}

export function useJob(jobId: string | null) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<JobWithDetailPublic>({
    queryKey: ['jobs', jobId],
    queryFn: async () => {
      const r = await api.get<JobWithDetailPublic>(`/jobs/${jobId}`);
      return r.data;
    },
    enabled: !!accessToken && !!jobId,
    retry: false,
  });
}
