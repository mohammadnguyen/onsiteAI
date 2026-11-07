import { useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

/**
 * Potential-variation flags whose result is not known yet (DEC-PV-001,
 * reliability "P").
 *
 * A deliberate copy of the task store (src/store/taskPendingCreates.ts),
 * with its own storage key: the founder chose two copies over refactoring
 * Task. The rules are the same.
 *
 * Written to the phone BEFORE the flag request is sent, removed once the
 * server gives a definite answer. If the answer never comes - a timeout, no
 * signal, the app killed mid-request - the record stays, and the next time
 * the user opens that entry's flag screen the app first ASKS the server
 * whether the flag exists (`GET /potential-variations?create_client_id=`).
 * Found: it opens that flag. Not found: it offers to send the same values
 * again under the SAME key, so even a request that was only slow cannot
 * become a second flag.
 *
 * What this is NOT: an offline queue. Nothing here sends anything by itself;
 * a record is resolved only when the user comes back to it.
 *
 * Per account: every record carries the user id it belongs to and the store
 * only hands out the current user's records. The explicit logout clears the
 * account's own records (src/store/session.ts); an involuntary one (a dead
 * token mid-shift) keeps them for the same worker's next sign-in.
 *
 * THE PHONE'S STORAGE IS THE TRUTH; memory is a copy of it. Every change is
 * read from disk, applied, and written back, one at a time - never written
 * from memory. A store that failed to load holds an empty list in memory
 * while other accounts' records still sit on disk; writing that list back
 * would erase them. If the disk cannot be read, nothing is written and the
 * caller is told.
 *
 * One thing lives in memory only: the flags this app is SENDING right now
 * (`sending`). A flag appears there the moment Save is tapped, before its
 * record has reached the disk, so a screen opened in that moment sees the
 * flag in progress instead of offering a second one under a new key.
 */
export type PendingPvCreate = {
  /** The idempotency key the request was sent with. Never regenerated. */
  create_client_id: string;
  user_id: string;
  site_log_event_id: string;
  /** The entry version the person saw when they tapped Save. */
  source_revision_no: number;
  title: string;
  created_at: number;
};

type State = {
  pending: PendingPvCreate[];
  /** Flags being sent right now. Memory only, never written to disk. */
  sending: PendingPvCreate[];
  /** The disk has been read successfully at least once. */
  hydrated: boolean;
  /** The last attempt to read the disk failed. */
  loadFailed: boolean;
  /** Read the disk into memory. Safe to call again after a failure. */
  hydrate: () => Promise<void>;
  /** Resolves once the record is on disk; rejects if it could not be. */
  putDurable: (p: PendingPvCreate) => Promise<void>;
  removeDurable: (createClientId: string) => Promise<void>;
  markSending: (p: PendingPvCreate) => void;
  unmarkSending: (createClientId: string) => void;
  /**
   * This account's unresolved flag for one entry, newest first: one being
   * sent right now, or one on disk whose answer never came.
   */
  forEvent: (userId: string, eventId: string) => PendingPvCreate | undefined;
  /** This account's records on disk. */
  forUser: (userId: string) => PendingPvCreate[];
  /** Explicit logout: drop exactly this account's records. */
  clearUser: (userId: string) => Promise<void>;
};

const PV_PENDING_STORAGE_KEY = 'pv-pending-creates';

/** Throws when the disk cannot be read or holds something unreadable. */
async function readDisk(): Promise<PendingPvCreate[]> {
  const raw = await AsyncStorage.getItem(PV_PENDING_STORAGE_KEY);
  if (raw === null) return [];
  const parsed = JSON.parse(raw) as { state?: { pending?: unknown } };
  const list = parsed?.state?.pending;
  if (!Array.isArray(list)) throw new Error('unreadable pending-flag store');
  return list as PendingPvCreate[];
}

async function writeDisk(list: PendingPvCreate[]): Promise<void> {
  await AsyncStorage.setItem(
    PV_PENDING_STORAGE_KEY,
    JSON.stringify({ state: { pending: list }, version: 0 }),
  );
}

// One disk operation at a time, so two changes can never interleave their
// read and their write and lose one of them.
let chain: Promise<unknown> = Promise.resolve();
function serial<T>(work: () => Promise<T>): Promise<T> {
  const run = chain.then(work, work);
  chain = run.catch(() => undefined);
  return run;
}

export const usePvPendingCreates = create<State>()((set, get) => {
  const change = (apply: (list: PendingPvCreate[]) => PendingPvCreate[]) =>
    serial(async () => {
      const next = apply(await readDisk());
      await writeDisk(next);
      set({ pending: next, hydrated: true, loadFailed: false });
    });

  return {
    pending: [],
    sending: [],
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
    markSending: (p) =>
      set((st) => ({
        sending: [p, ...st.sending.filter((x) => x.create_client_id !== p.create_client_id)],
      })),
    unmarkSending: (id) =>
      set((st) => ({ sending: st.sending.filter((x) => x.create_client_id !== id) })),
    forEvent: (userId, eventId) =>
      [...get().sending, ...get().pending]
        .filter((p) => p.user_id === userId && p.site_log_event_id === eventId)
        .sort((a, b) => b.created_at - a.created_at)[0],
    forUser: (userId) => get().pending.filter((p) => p.user_id === userId),
    clearUser: (userId) => change((list) => list.filter((p) => p.user_id !== userId)),
  };
});

// Load what an earlier run left on the phone, as the store first comes up.
void usePvPendingCreates.getState().hydrate();

/**
 * True once the records persisted by an earlier run are loaded.
 *
 * A screen that decides "is there an unresolved flag for this entry?" must
 * not decide before then: an empty store that has not loaded yet looks
 * exactly like "nothing pending", and answering that would let the user
 * start a second flag under a new key. A failed load is retried each time a
 * screen asks.
 */
export function usePvPendingHydrated(): { hydrated: boolean; loadFailed: boolean } {
  const hydrated = usePvPendingCreates((s) => s.hydrated);
  const loadFailed = usePvPendingCreates((s) => s.loadFailed);
  useEffect(() => {
    if (!hydrated) void usePvPendingCreates.getState().hydrate();
  }, [hydrated]);
  return { hydrated, loadFailed };
}
