import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

export type WorkerPublic = components['schemas']['WorkerPublic'];
export type LabourEntryPublic = components['schemas']['LabourEntryPublic'];

/**
 * L-B1 labour attendance hooks (read roster, read day entries, save).
 *
 * Attendance/days language only — no payroll concepts. The summary
 * endpoint (admin-only) is deliberately NOT wired here; it ships with
 * the L-B2 attendance-summary screen.
 */

/**
 * Worker roster (any authenticated caller; backend returns
 * display_name-ordered rows).
 *
 * The tick screen always fetches with `includeInactive: true`: a
 * since-deactivated worker can still hold an entry on the selected
 * date, and that row must render with its name. The screen filters
 * what to DISPLAY (active OR has-entry-on-date); the roster fetch is
 * the superset.
 */
export function useWorkers(includeInactive = false) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<WorkerPublic[]>({
    queryKey: ['workers', { includeInactive }],
    queryFn: async () => {
      const r = await api.get<WorkerPublic[]>('/workers', {
        params: includeInactive ? { include_inactive: true } : undefined,
      });
      return r.data;
    },
    enabled: !!accessToken,
    retry: false,
  });
}

/**
 * Attendance entries for one (job, date) — the tick screen's prefill
 * query. ``from``/``to`` are both pinned to the selected date. The
 * unique (worker, job, date) constraint guarantees at most one row
 * per worker here, so limit 200 comfortably covers any roster.
 *
 * staleTime 0 (default): several recorders can write the same morning;
 * always refetch on mount/refocus rather than trusting a cached day.
 */
export function useLabourEntries(jobId: string | null, date: string | null) {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery<LabourEntryPublic[]>({
    queryKey: ['labour-entries', { jobId, date }],
    queryFn: async () => {
      const r = await api.get<LabourEntryPublic[]>('/labour-entries', {
        params: { job_id: jobId, from: date, to: date, limit: 200 },
      });
      return r.data;
    },
    enabled: !!accessToken && !!jobId && !!date,
    retry: false,
  });
}

/**
 * Thrown when the delete phase of a save fails. Carries the worker_id
 * so the screen can name the affected worker, and the underlying
 * axios error so the server detail can be surfaced.
 */
export class AttendanceDeleteError extends Error {
  workerId: string;
  cause: unknown;

  constructor(workerId: string, cause: unknown) {
    super('Attendance delete failed');
    this.name = 'AttendanceDeleteError';
    this.workerId = workerId;
    this.cause = cause;
  }
}

export type AttendanceSaveInput = {
  jobId: string;
  /** ISO YYYY-MM-DD work date for the whole save. */
  date: string;
  /** Existing entries to remove (unticked rows the caller may modify). */
  deletes: { entryId: string; workerId: string }[];
  /** New/changed ticks. day_fraction is a JSON number (0.5 | 1). */
  batch: { worker_id: string; day_fraction: number }[];
};

export type AttendanceSaveResult = {
  deletedCount: number;
  saved: LabourEntryPublic[];
};

/**
 * The approved two-phase diff save (L-B1):
 *
 *   1. DELETE each unticked-but-existing permitted entry (sequential;
 *      aborts on the FIRST failure so the state stays obvious).
 *   2. One POST /labour-entries/batch for ticked new/changed rows —
 *      all-or-nothing server-side (cross-job <=1.0/day enforced there).
 *
 * The two phases are NOT atomic with each other — acknowledged v1
 * shortcut. Mitigation lives here: ``onSettled`` invalidates the
 * labour caches on success AND failure, so the screen always re-renders
 * server truth after any save attempt. Re-sending the same batch is
 * idempotent (server upserts per worker/job/date).
 */
export function useSaveAttendance() {
  const qc = useQueryClient();
  return useMutation<AttendanceSaveResult, unknown, AttendanceSaveInput>({
    mutationFn: async ({ jobId, date, deletes, batch }) => {
      for (const d of deletes) {
        try {
          await api.delete(`/labour-entries/${d.entryId}`);
        } catch (err) {
          // 404 = already gone (another recorder removed it, or a
          // retry against a stale entry_id). The declarative intent —
          // "this worker has no entry" — is satisfied; treat as done.
          if (axios.isAxiosError(err) && err.response?.status === 404) {
            continue;
          }
          throw new AttendanceDeleteError(d.workerId, err);
        }
      }
      let saved: LabourEntryPublic[] = [];
      if (batch.length > 0) {
        const { data } = await api.post<LabourEntryPublic[]>(
          '/labour-entries/batch',
          { job_id: jobId, work_date: date, entries: batch },
        );
        saved = data;
      }
      return { deletedCount: deletes.length, saved };
    },
    onSuccess: (result, vars) => {
      // Seed the day's cache from the mutation result so the checklist
      // never flashes back to pre-save state while the invalidation
      // refetch is in flight (seconds on a weak network). The refetch
      // below remains the consistency pass.
      qc.setQueryData<LabourEntryPublic[]>(
        ['labour-entries', { jobId: vars.jobId, date: vars.date }],
        (old) => {
          const deletedIds = new Set(vars.deletes.map((d) => d.entryId));
          const savedByWorker = new Map(
            result.saved.map((e) => [e.worker_id, e]),
          );
          const kept = (old ?? []).filter(
            (e) => !deletedIds.has(e.entry_id) && !savedByWorker.has(e.worker_id),
          );
          return [...result.saved, ...kept];
        },
      );
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['labour-entries'] });
      // L-B2's summary screen will read this root; invalidating now
      // keeps the matrix complete from day one at zero cost.
      void qc.invalidateQueries({ queryKey: ['labour-summary'] });
    },
  });
}
