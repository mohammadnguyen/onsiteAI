import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type UserPublic = components['schemas']['UserPublic']
type UserInvite = components['schemas']['UserInvite']
type UserUpdate = components['schemas']['UserUpdate']

export function useUsers() {
  return useQuery({
    queryKey: ['users'],
    queryFn: async (): Promise<UserPublic[]> => {
      const { data } = await api.get<UserPublic[]>('/users')
      return data
    },
  })
}

export function useInviteUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: UserInvite): Promise<UserPublic> => {
      const { data } = await api.post<UserPublic>('/users/invite', body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['users'] })
    },
  })
}

export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      userId,
      body,
    }: {
      userId: string
      body: UserUpdate
    }): Promise<UserPublic> => {
      const { data } = await api.patch<UserPublic>(`/users/${userId}`, body)
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
