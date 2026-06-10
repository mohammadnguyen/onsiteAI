import { create } from 'zustand';
import type { components } from '../api/types';

type ReviewStatus = components['schemas']['ReviewStatus'];

export type ExpenseListDatePreset = 'all' | 'week' | 'month';

/**
 * M2-B: in-memory filter selections for the full expenses list.
 *
 * The root layout renders a plain `Slot`, so `/expenses/list` is
 * UNMOUNTED whenever the user drills into `/expenses/[id]` and
 * remounted on return (expo-router's SlotNavigator renders only the
 * focused sibling). Component-local useState would therefore lose
 * every filter selection on each row tap. Keeping the selections in
 * a tiny store (same pattern as `store/selectedJob`) preserves them
 * across that round trip; React Query's cached pages make the
 * remount cheap.
 *
 * Deliberately NOT persisted: filters are session-scoped workflow
 * state, not durable user data — an app restart opening the default
 * view is correct. Scroll position is still lost on remount
 * (FlatList component state); a navigation-architecture change
 * (Stack layout keeping the list mounted) is out of M2-B scope and
 * would need its own approval.
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
