import { useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

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
 *
 * THE PHONE'S STORAGE IS THE TRUTH; memory is a copy of it. Every change is
 * read from disk, applied, and written back, one at a time - never written
 * from memory. A store that failed to load holds an empty list in memory
 * while other accounts' records still sit on disk; writing that list back
 * (which a persist middleware does on every change) would erase them, and
 * with them the only way those creates can be recovered without a duplicate.
 * If the disk cannot be read, nothing is written and the caller is told.
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
  /** The disk has been read successfully at least once. */
  hydrated: boolean;
  /** The last attempt to read the disk failed. */
  loadFailed: boolean;
  /** Read the disk into memory. Safe to call again after a failure. */
  hydrate: () => Promise<void>;
  /** Resolves once the record is on disk; rejects if it could not be. */
  putDurable: (p: PendingTaskCreate) => Promise<void>;
  removeDurable: (createClientId: string) => Promise<void>;
  /** This account's unresolved create for one entry, newest first. */
  forEvent: (userId: string, eventId: string) => PendingTaskCreate | undefined;
  forUser: (userId: string) => PendingTaskCreate[];
  /** Explicit logout: drop exactly this account's records. */
  clearUser: (userId: string) => Promise<void>;
};

const STORAGE_KEY = 'task-pending-creates';

/** Throws when the disk cannot be read or holds something unreadable. */
async function readDisk(): Promise<PendingTaskCreate[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  if (raw === null) return [];
  const parsed = JSON.parse(raw) as { state?: { pending?: unknown } };
  const list = parsed?.state?.pending;
  if (!Array.isArray(list)) throw new Error('unreadable pending-create store');
  return list as PendingTaskCreate[];
}

async function writeDisk(list: PendingTaskCreate[]): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ state: { pending: list }, version: 0 }));
}

// One disk operation at a time, so two changes can never interleave their
// read and their write and lose one of them.
let chain: Promise<unknown> = Promise.resolve();
function serial<T>(work: () => Promise<T>): Promise<T> {
  const run = chain.then(work, work);
  chain = run.catch(() => undefined);
  return run;
}

export const useTaskPendingCreates = create<State>()((set, get) => {
  const change = (apply: (list: PendingTaskCreate[]) => PendingTaskCreate[]) =>
    serial(async () => {
      const next = apply(await readDisk());
      await writeDisk(next);
      set({ pending: next, hydrated: true, loadFailed: false });
    });

  return {
    pending: [],
    hydrated: false,
    loadFailed: false,
    hydrate: () =>
      serial(async () => {
        try {
          set({ pending: await readDisk(), hydrated: true, loadFailed: false });
        } catch {
          set({ loadFailed: true });
        }
      }),
    putDurable: (p) =>
      change((list) => [p, ...list.filter((x) => x.create_client_id !== p.create_client_id)]),
    removeDurable: (id) => change((list) => list.filter((x) => x.create_client_id !== id)),
    forEvent: (userId, eventId) =>
      get()
        .pending.filter((p) => p.user_id === userId && p.site_log_event_id === eventId)
        .sort((a, b) => b.created_at - a.created_at)[0],
    forUser: (userId) => get().pending.filter((p) => p.user_id === userId),
    clearUser: (userId) => change((list) => list.filter((p) => p.user_id !== userId)),
  };
});

// Load what an earlier run left on the phone, as the store first comes up.
void useTaskPendingCreates.getState().hydrate();

/**
 * True once the records persisted by an earlier run are loaded.
 *
 * A screen that decides "is there an unresolved create for this entry?"
 * must not decide before then: an empty store that has not loaded yet looks
 * exactly like "nothing pending", and answering that would let the user
 * start a second create under a new key. A failed load is retried each time
 * a screen asks.
 */
export function usePendingCreatesHydrated(): { hydrated: boolean; loadFailed: boolean } {
  const hydrated = useTaskPendingCreates((s) => s.hydrated);
  const loadFailed = useTaskPendingCreates((s) => s.loadFailed);
  useEffect(() => {
    if (!hydrated) void useTaskPendingCreates.getState().hydrate();
  }, [hydrated]);
  return { hydrated, loadFailed };
}
