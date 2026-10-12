import { useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

/**
 * Task creates whose result is not known yet (plan v2 §5).
 *
 * Written to the phone BEFORE the create request is sent, removed once the
 * server gives a definite answer. If the answer never comes - a timeout, no
 * signal, the app killed mid-request - the record stays, and the next time
 * the user opens that entry's create screen the app first ASKS the server
 * whether the task exists (`GET /tasks?create_client_id=`). Found: it opens
 * that task. Not found: it offers to send the same values again under the
 * SAME key, so even a request that was only slow cannot become a second task.
 *
 * What this is NOT: an offline queue. Nothing here sends anything by itself;
 * a record is resolved only when the user comes back to it.
 *
 * Per account: every record carries the user id it belongs to and the store
 * only hands out the current user's records. The explicit logout clears the
 * account's own records; an involuntary one (a dead token mid-shift) keeps
 * them for the same worker's next sign-in.
 */
export type PendingTaskCreate = {
  /** The idempotency key the request was sent with. Never regenerated. */
  create_client_id: string;
  user_id: string;
  site_log_event_id: string;
  title: string;
  assignee_user_id: string | null;
  /** For display while unresolved; the server never sees it. */
  assignee_name: string | null;
  due_date: string | null;
  created_at: number;
};

type State = {
  pending: PendingTaskCreate[];
  /** Resolves once the persisted copy has actually been written. */
  putDurable: (p: PendingTaskCreate) => Promise<void>;
  removeDurable: (createClientId: string) => Promise<void>;
  /** This account's unresolved create for one entry, newest first. */
  forEvent: (userId: string, eventId: string) => PendingTaskCreate | undefined;
  forUser: (userId: string) => PendingTaskCreate[];
  /** Explicit logout: drop exactly this account's records. */
  clearUser: (userId: string) => Promise<void>;
};

const STORAGE_KEY = 'task-pending-creates';

/**
 * The persist middleware writes in the background; this repeats the write
 * with the same key and shape and waits for it, so "written before the
 * request is sent" is true rather than likely.
 */
async function flush(): Promise<void> {
  const state = { pending: useTaskPendingCreates.getState().pending };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

export const useTaskPendingCreates = create<State>()(
  persist(
    (set, get) => ({
      pending: [],
      putDurable: async (p) => {
        set((s) => ({
          pending: [p, ...s.pending.filter((x) => x.create_client_id !== p.create_client_id)],
        }));
        await flush();
      },
      removeDurable: async (id) => {
        set((s) => ({ pending: s.pending.filter((x) => x.create_client_id !== id) }));
        await flush();
      },
      forEvent: (userId, eventId) =>
        get()
          .pending.filter((p) => p.user_id === userId && p.site_log_event_id === eventId)
          .sort((a, b) => b.created_at - a.created_at)[0],
      forUser: (userId) => get().pending.filter((p) => p.user_id === userId),
      clearUser: async (userId) => {
        set((s) => ({ pending: s.pending.filter((p) => p.user_id !== userId) }));
        await flush();
      },
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => AsyncStorage),
    },
  ),
);

/**
 * True once the records persisted by an earlier run are loaded.
 *
 * A screen that decides "is there an unresolved create for this entry?"
 * must not decide before then: an empty store that has not loaded yet
 * looks exactly like "nothing pending", and answering that would let the
 * user start a second create under a new key.
 */
export function usePendingCreatesHydrated(): boolean {
  const [hydrated, setHydrated] = useState(() => useTaskPendingCreates.persist.hasHydrated());
  useEffect(() => {
    const unsubscribe = useTaskPendingCreates.persist.onFinishHydration(() => setHydrated(true));
    setHydrated(useTaskPendingCreates.persist.hasHydrated());
    return unsubscribe;
  }, []);
  return hydrated;
}
