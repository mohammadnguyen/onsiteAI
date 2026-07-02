import { create } from 'zustand';

/**
 * L-E1: cross-screen "open this day for editing" handoff.
 *
 * The records screen (/labour/records) lets the user tap a saved entry
 * to edit that job+date on the Labour tab's tick screen. Tab screens
 * can't receive route params cleanly, so the target rides in this
 * store (same pattern as selectedJob for the Jobs modal): records sets
 * it, navigates, and the Labour tab consumes + clears it on focus.
 *
 * One-shot semantics: the tab CLEARS the target as soon as it applies
 * it, so a stale target can never re-hijack the screen on a later
 * focus.
 */
type LabourEditTarget = { jobId: string; date: string };

type State = {
  target: LabourEditTarget | null;
  setTarget: (target: LabourEditTarget) => void;
  clear: () => void;
};

export const useLabourEditTargetStore = create<State>((set) => ({
  target: null,
  setTarget: (target) => set({ target }),
  clear: () => set({ target: null }),
}));
