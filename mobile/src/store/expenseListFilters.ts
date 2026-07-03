import { create } from 'zustand';
import type { components } from '../api/types';

type ReviewStatus = components['schemas']['ReviewStatus'];

export type ExpenseListDatePreset = 'all' | 'week' | 'month';

/**
 * M2-B: in-memory filter selections for the full expenses list.
 *
 * Originally required because the root layout rendered a plain
 * `Slot`, which UNMOUNTED `/expenses/list` on every drill-in to
 * `/expenses/[id]` — component-local useState lost every filter
 * selection on each row tap. The root Stack (back-nav fix) now keeps
 * the list mounted across that round trip, but the store stays: it
 * still covers app-level remounts, and logout resets it via
 * `store/session.ts` (audit B-02) so one user's filters can't leak
 * to the next.
 *
 * Deliberately NOT persisted: filters are session-scoped workflow
 * state, not durable user data — an app restart opening the default
 * view is correct.
 */
type ExpenseListFiltersState = {
  jobId: string | null;
  status: ReviewStatus | null;
  datePreset: ExpenseListDatePreset;
  supplierId: string | null;
  categoryId: string | null;
  setJobId: (v: string | null) => void;
  setStatus: (v: ReviewStatus | null) => void;
  setDatePreset: (v: ExpenseListDatePreset) => void;
  setSupplierId: (v: string | null) => void;
  setCategoryId: (v: string | null) => void;
};

export const useExpenseListFiltersStore = create<ExpenseListFiltersState>()(
  (set) => ({
    jobId: null,
    status: null,
    datePreset: 'all',
    supplierId: null,
    categoryId: null,
    setJobId: (v) => set({ jobId: v }),
    setStatus: (v) => set({ status: v }),
    setDatePreset: (v) => set({ datePreset: v }),
    setSupplierId: (v) => set({ supplierId: v }),
    setCategoryId: (v) => set({ categoryId: v }),
  }),
);
