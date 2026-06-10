import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type UserPublic = components['schemas']['UserPublic'];
export type UserInviteInput = components['schemas']['UserInvite'];
export type UserUpdateInput = components['schemas']['UserUpdate'];
export type UserRole = components['schemas']['UserRole'];

/**
 * M4 — GET /users (admin-only; contributors get 403, which the
 * screen maps to the "admins only" state). Returns every user with
 * role + is_active. Cached under the ['users'] root; only the two
 * mutations below change users, so they own the invalidation.
 */
export function useUsers() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<UserPublic[]>({
    queryKey: ['users'],
    queryFn: async () => {
      const r = await api.get<UserPublic[]>('/users');
      return r.data;
    },
    enabled: !!accessToken,
    staleTime: 0,
    retry: false,
  });
}

/**
 * M4 — POST /users/invite (admin-only).
 *
 * The backend's Phase 1 design: the admin supplies
 * ``initial_password`` directly and communicates it to the new user
 * out of band — there is NO email delivery and NO self-service
 * reset yet. The password is sent over HTTPS in the request body
 * and is never logged or stored client-side.
 *
 * 409 = duplicate email OR inviting an admin beyond the active-admin
 * cap; the caller surfaces the backend detail string verbatim.
 */
export function useInviteUser() {
  const qc = useQueryClient();
  return useMutation<UserPublic, unknown, UserInviteInput>({
    mutationFn: async (body) => {
      const { data } = await api.post<UserPublic>('/users/invite', body);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

/**
 * M4 — PATCH /users/{id} (admin-only).
 *
 * Takes {userId, patch} per call (NOT per hook instantiation) so a
 * list screen can act on any row without hooks-in-loops — same shape
 * precedent as useCreateJobAlias.
 *
 * Backend semantics the UI relies on:
 *   - is_active=false force-401s the target's existing tokens on
 *     their next request (immediate sign-out).
 *   - 409 on admin-cap breach or last-admin removal — detail string
 *     surfaced verbatim by the caller.
 * The UI separately BLOCKS self-deactivation/self-demotion (the
 * backend allows them when not last-admin; on a phone that's an
 * instant self-logout footgun with no legitimate use).
 */
export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation<
    UserPublic,
    unknown,
    { userId: string; patch: UserUpdateInput }
  >({
    mutationFn: async ({ userId, patch }) => {
      const { data } = await api.patch<UserPublic>(`/users/${userId}`, patch);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['users'] });
    },
  });
}
