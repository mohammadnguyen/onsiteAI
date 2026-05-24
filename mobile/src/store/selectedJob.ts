import { create } from 'zustand';

/**
 * Persistent "currently-open job" state for the Jobs tab modal.
 *
 * Why: JobsScreen used a local `useState` for which job's detail modal
 * was open. When the user tapped an expense row inside the modal and
 * navigated to /expenses/{id}, JobsScreen could unmount (depending on
 * tab navigator behaviour) and the selected id was lost. On return,
 * the modal didn't re-open and the user landed on the Capture screen
 * (the tab navigator's default tab).
 *
 * Hoisting the id into a zustand store keeps it alive across React
 * unmount/remount cycles, so JobsScreen can re-open the modal at the
 * same job after a round-trip through the expense detail screen.
 *
 * Discipline (operator guardrail): consumers reading from this store
 * for navigation-back behaviour MUST also gate on an explicit URL
 * query param (`from=job&jobId=...`). The store alone is NOT a
 * trigger to "return to a job context"; it's just persistence for
 * the modal's open/closed state. This prevents a stale modal from
 * being misinterpreted as a navigation-context signal by other
 * screens.
 */
type State = {
  selectedJobId: string | null;
  setSelectedJobId: (id: string | null) => void;
};

export const useSelectedJobStore = create<State>((set) => ({
  selectedJobId: null,
  setSelectedJobId: (id) => set({ selectedJobId: id }),
}));
