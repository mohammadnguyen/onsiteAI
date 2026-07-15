import { create } from 'zustand';

/**
 * Labour-tab session state.
 *
 * B4-2 slimmed this store: the one-shot "edit this day" target
 * (records → attendance handoff) is RETIRED — records is an embedded
 * tab now and hands off via a plain callback. What remains is the
 * in-session memory of the last job picked on the tick screen
 * (selection DEFAULT, read via getState(), reset on logout — audit
 * B-02).
 */
type State = {
  lastUsedJobId: string | null;
  setLastUsedJobId: (id: string | null) => void;
};

export const useLabourEditTargetStore = create<State>((set) => ({
  lastUsedJobId: null,
  setLastUsedJobId: (id) => set({ lastUsedJobId: id }),
}));
