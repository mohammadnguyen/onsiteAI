import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type UserPublic = components['schemas']['UserPublic'];
export type TokenPair = components['schemas']['TokenPair'];

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<UserPublic>({
    queryKey: ['auth', 'me'],
    queryFn: async () => {
      const r = await api.get<UserPublic>('/auth/me');
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}
