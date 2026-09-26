import { queryClient } from '../api/queryClient';
import { useFailuresStore } from './failures';
import { useSiteLogDrafts } from './siteLogDrafts';
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
 *    survive the re-login. Only the explicit Settings logout wipes
 *    them (see wipeOnExplicitLogout below).
 *  - language + font-size preferences (device-level, not
 *    user-level) and the auth store (owns tokens).
 *
 * Keep this list in sync when adding a store that holds user-scoped
 * data.
 */
export function resetSessionState(): void {
  queryClient.clear();
  useLabourEditTargetStore.getState().setLastUsedJobId(null);
  const filters = useExpenseListFiltersStore.getState();
  filters.setJobId(null);
  filters.setStatus(null);
  filters.setDatePreset('all');
  filters.setSupplierId(null);
  filters.setCategoryId(null);
}

/**
 * Explicit-logout extra: the cleanup an involuntary logout must NOT do.
 *
 * Split from resetSessionState() so a session dying mid-shift never
 * destroys the same worker's unsent work (see the doc comment above).
 * Settings' logout calls both.
 *
 * SCOPED TO THE ACCOUNT SIGNING OUT, AND TO WHAT IT AGREED TO LOSE. This
 * used to clear every account's site log drafts and delete the whole
 * `site-log/` tree, so worker B logging out destroyed worker A's unsent
 * photos, recordings and documents — files that exist nowhere else —
 * silently.
 *
 * `confirmedCaptureIds` is the exact list the user was shown and accepted.
 * It is not recomputed here on purpose: logout awaits `/auth/logout`
 * first, and during that wait the user can still reach the capture screen
 * and save something. A list computed at deletion time would take that new
 * capture too, after a confirmation that never mentioned it. An empty list
 * deletes nothing, which is what an ordinary logout with no unsent work
 * should do.
 *
 * `userId` null means no identifiable account, and then nothing is
 * touched: deleting somebody's evidence is not the safe default.
 */
export async function wipeOnExplicitLogout(
  userId: string | null,
  confirmedCaptureIds: string[],
): Promise<void> {
  useFailuresStore.getState().clearFailures();
  if (userId === null || confirmedCaptureIds.length === 0) return;
  await useSiteLogDrafts.getState().clearCaptures(userId, confirmedCaptureIds);
}
