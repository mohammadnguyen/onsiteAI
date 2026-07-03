import { queryClient } from '../api/queryClient';
import { useFailuresStore } from './failures';
import { useSelectedJobStore } from './selectedJob';
import { useLabourEditTargetStore } from './labourEditTarget';
import { useExpenseListFiltersStore } from './expenseListFilters';

/**
 * Audit B-02: one place that wipes user-scoped state when the
 * session ends. Without this, the next login on a shared device
 * could read the PREVIOUS user's React Query cache (role, job
 * money, expense lists) and cross-screen selections.
 *
 * Token clearing itself stays in useAuthStore.clear() — this covers
 * everything AROUND the tokens. Called from the root layout's auth
 * redirect (the single choke point every logout path crosses:
 * manual logout, terminal 401, dead refresh token). Also fires once
 * on a logged-out cold start, where it is a no-op on empty state.
 *
 * Deliberately NOT reset here:
 *  - persisted failed-capture texts (M0 failures store): an
 *    INVOLUNTARY logout (token death mid-shift) is almost certainly
 *    the same user, whose typed-but-unsent capture text must
 *    survive the re-login. Only the explicit Settings logout — a
 *    deliberate device handoff — wipes them (see wipeFailures
 *    there).
 *  - language + font-size preferences (device-level, not
 *    user-level) and the auth store (owns tokens).
 *
 * Keep this list in sync when adding a store that holds user-scoped
 * data.
 */
export function resetSessionState(): void {
  queryClient.clear();
  useSelectedJobStore.getState().setSelectedJobId(null);
  const labour = useLabourEditTargetStore.getState();
  labour.clear();
  labour.setLastUsedJobId(null);
  const filters = useExpenseListFiltersStore.getState();
  filters.setJobId(null);
  filters.setStatus(null);
  filters.setDatePreset('all');
  filters.setSupplierId(null);
  filters.setCategoryId(null);
}

/**
 * Explicit-logout extra: wipe the persisted failed-capture texts.
 * Split from resetSessionState() so an involuntary session death
 * never destroys the same worker's unsent capture text (see the
 * doc comment above). Settings' logout calls both.
 */
export function wipeFailures(): void {
  useFailuresStore.getState().clearFailures();
}
