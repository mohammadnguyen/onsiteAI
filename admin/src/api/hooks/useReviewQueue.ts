import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { components } from '../types'

type ReviewQueuePublic = components['schemas']['ReviewQueuePublic']
type ReviewQueueDetail = components['schemas']['ReviewQueueDetail']
type ReviewQueueStatus = components['schemas']['ReviewQueueStatus']
type ResolveRequest = components['schemas']['ResolveRequest']
type RejectRequest = components['schemas']['RejectRequest']

export function useReviewQueue(status: ReviewQueueStatus = 'open') {
  return useQuery({
    queryKey: ['review-queue', status],
    queryFn: async (): Promise<ReviewQueuePublic[]> => {
      const { data } = await api.get<ReviewQueuePublic[]>('/review-queue', {
        params: { status },
      })
      return data
    },
  })
}

export function useReviewQueueItem(reviewId: string | undefined) {
  return useQuery({
    queryKey: ['review-queue', 'detail', reviewId],
    enabled: !!reviewId,
    queryFn: async (): Promise<ReviewQueueDetail> => {
      const { data } = await api.get<ReviewQueueDetail>(`/review-queue/${reviewId}`)
      return data
    },
  })
}

export function useResolveReview() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      reviewId,
      body,
    }: {
      reviewId: string
      body: ResolveRequest
    }): Promise<ReviewQueuePublic> => {
      const { data } = await api.post<ReviewQueuePublic>(
        `/review-queue/${reviewId}/resolve`,
        body,
      )
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
      void qc.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

export function useRejectReview() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({
      reviewId,
      body,
    }: {
      reviewId: string
      body: RejectRequest
    }): Promise<ReviewQueuePublic> => {
      const { data } = await api.post<ReviewQueuePublic>(
        `/review-queue/${reviewId}/reject`,
        body,
      )
      return data
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
      void qc.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}
