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
  /** In-session memory of the last job picked on the Labour tab's
   *  tick screen. Previously a module-level `let` inside the tab
   *  screen; moved here (audit B-02) so logout can reset it. Read
   *  via getState() — it is a selection DEFAULT, not reactive UI
   *  state. */
  lastUsedJobId: string | null;
  setTarget: (target: LabourEditTarget) => void;
  setLastUsedJobId: (id: string | null) => void;
  /** Clears the one-shot target ONLY — lastUsedJobId survives so the
   *  tick screen keeps its job default. Session reset (store/session)
   *  clears both. */
  clear: () => void;
};

export const useLabourEditTargetStore = create<State>((set) => ({
  target: null,
  lastUsedJobId: null,
  setTarget: (target) => set({ target }),
  setLastUsedJobId: (id) => set({ lastUsedJobId: id }),
  clear: () => set({ target: null }),
}));
