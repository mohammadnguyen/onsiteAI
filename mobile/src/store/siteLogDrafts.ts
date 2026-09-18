import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { AttachmentState, CaptureStatus, Declaration, MediaType } from '../api/siteLog';
import { releaseAllRetained, releaseCapture } from '../siteLog/files';

/**
 * Local drafts for Site Log capture.
 *
 * What this is for: nothing the user typed, chose or recorded should be lost
 * because a request timed out, the app was backgrounded, or the phone
 * restarted before the server confirmed the save.
 *
 * What this is NOT: a background sync engine. Nothing here uploads on its own.
 * A draft is resumed only when the user opens it and asks.
 *
 * Boundaries, stated because they are easy to assume away:
 *  - Per account. Every draft records the user id it belongs to and the store
 *    only ever exposes the current user's drafts, so a shared device cannot
 *    show one worker another's unsent capture.
 *  - ATTACHMENT FILES ARE COPIED, before a draft records them. The picker's
 *    own URI is in the OS cache and can be reclaimed at any time; the copy
 *    lives in the app's document directory, under the account and the
 *    capture that own it (see src/siteLog/files.ts). `retained` says so per
 *    attachment, and a draft is never recorded as holding a file it does
 *    not hold. A resumed draft still verifies each file - app data can be
 *    cleared - and marks what is gone as `missing` rather than failing an
 *    upload later with something unexplainable.
 *  - Bounded, but never by throwing work away. A new capture is REFUSED
 *    when this account already holds MAX_DRAFTS unfinished ones, and the
 *    user is told; the store itself evicts nothing. Silently dropping the
 *    oldest draft to make room destroys text and file references that
 *    exist nowhere else - during a long outage that is exactly the work
 *    the user most needs back. The cap is per account, so one person's
 *    unfinished captures cannot crowd out another's.
 *  - Never tokens, never credentials, never raw response payloads.
 */

export type DraftAttachmentStatus = AttachmentState | 'missing';

export type DraftAttachment = {
  attachment_client_id: string;
  /** Derived from the file's MIME exactly as the server derives it. */
  media_type: MediaType;
  /** The app's own copy, not the picker's cache path - see `retained`. */
  uri: string;
  /**
   * True when `uri` is this app's own copy, made before the draft recorded
   * the attachment. Optional only so a draft persisted by an earlier build
   * still loads; absent means "not known to be kept".
   */
  retained?: boolean;
  name: string;
  mime: string;
  size: number | null;
  /** Server truth where known; 'awaiting_upload' until the server says otherwise. */
  status: DraftAttachmentStatus;
};

export type DraftServerState = {
  site_log_event_id: string;
  capture_status: CaptureStatus;
  /** Server time of the last successful read, for display only. */
  observed_at: number;
};

export type SiteLogDraft = {
  /** Stable for the life of this logical capture. Never regenerated. */
  capture_client_id: string;
  user_id: string;
  created_at: number;
  updated_at: number;
  /** The declaration exactly as first submitted, replayed verbatim on retry. */
  declaration: Declaration | null;
  body_text: string;
  job_id: string | null;
  attachments: DraftAttachment[];
  /** Set once the server has acknowledged the declare. */
  server: DraftServerState | null;
  /**
   * True when a request failed in a way that does NOT tell us whether the
   * server saved anything — a timeout or a transport error. The UI must say
   * "not confirmed", never "failed", and the next attempt must ask the server
   * before doing anything else.
   */
  unconfirmed: boolean;
  /** Last user-facing message, kept so the reason survives a restart. */
  last_message: string | null;
};

type State = {
  drafts: SiteLogDraft[];
  upsert: (d: SiteLogDraft) => void;
  /** Resolves once the persisted copy has actually been written. */
  upsertDurable: (d: SiteLogDraft) => Promise<void>;
  patchDurable: (captureClientId: string, p: Partial<SiteLogDraft>) => Promise<void>;
  patch: (captureClientId: string, p: Partial<SiteLogDraft>) => void;
  remove: (captureClientId: string) => void;
  /**
   * Remove a draft AND delete the files kept for it. Only ever called once
   * the server has confirmed the capture saved, or the user has discarded
   * it; it deletes that capture's own directory, so no file another unsent
   * draft points at can be caught by it.
   */
  removeAndRelease: (captureClientId: string) => Promise<void>;
  forUser: (userId: string) => SiteLogDraft[];
  /** True when this account may not start another capture until one ends. */
  atCapacity: (userId: string) => boolean;
  /**
   * Captures with a submission running right now.
   *
   * Kept in the store, not in a screen: the screen that started one can be
   * navigated away from and reopened, and the new instance must still know
   * that discarding would delete the recovery information and the files a
   * live submission is using.
   */
  submitting: string[];
  beginSubmit: (captureClientId: string) => void;
  endSubmit: (captureClientId: string) => void;
  get: (captureClientId: string) => SiteLogDraft | undefined;
  clearAll: () => void;
};

const MAX_DRAFTS = 20;
const STORAGE_KEY = 'site-log-drafts';

/**
 * Write the current drafts to storage and wait for it.
 *
 * The persist middleware's own write is fire-and-forget; this repeats it
 * synchronously with the same key and shape so a caller can be sure the
 * recovery information is on disk before it sends anything.
 */
async function flushDrafts(): Promise<void> {
  const state = { drafts: useSiteLogDrafts.getState().drafts };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

export const useSiteLogDrafts = create<State>()(
  persist(
    (set, get) => ({
      drafts: [],
      submitting: [],
      beginSubmit: (id) =>
        set((s) =>
          s.submitting.includes(id) ? s : { submitting: [...s.submitting, id] },
        ),
      endSubmit: (id) => set((s) => ({ submitting: s.submitting.filter((x) => x !== id) })),
      upsert: (d) =>
        set((s) => ({
          // No slice: see the capacity note above. Nothing already here is
          // dropped to make room for this.
          drafts: [d, ...s.drafts.filter((x) => x.capture_client_id !== d.capture_client_id)],
        })),
      // zustand's persist middleware writes asynchronously, so a plain
      // set() gives no guarantee the recovery information survives a crash
      // a moment later. These await the write before the caller proceeds.
      upsertDurable: async (d) => {
        get().upsert(d);
        await flushDrafts();
      },
      patchDurable: async (id, p) => {
        get().patch(id, p);
        await flushDrafts();
      },
      patch: (id, p) =>
        set((s) => ({
          drafts: s.drafts.map((x) =>
            x.capture_client_id === id ? { ...x, ...p, updated_at: Date.now() } : x,
          ),
        })),
      remove: (id) =>
        set((s) => ({ drafts: s.drafts.filter((x) => x.capture_client_id !== id) })),
      removeAndRelease: async (id) => {
        const draft = get().get(id);
        get().remove(id);
        await flushDrafts();
        if (draft) await releaseCapture(draft.user_id, draft.capture_client_id);
      },
      forUser: (userId) => get().drafts.filter((d) => d.user_id === userId),
      atCapacity: (userId) =>
        get().drafts.filter((d) => d.user_id === userId).length >= MAX_DRAFTS,
      get: (id) => get().drafts.find((d) => d.capture_client_id === id),
      clearAll: () => {
        set({ drafts: [] });
        // The files belong to the drafts that were just discarded. Fire and
        // forget: this is called from a synchronous session teardown, and a
        // file that cannot be deleted must not block the logout.
        void releaseAllRetained();
      },
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => AsyncStorage),
      // Only the drafts are persisted. `submitting` describes this run of
      // the app; a restart has no submission in flight, and a stale entry
      // would lock a draft for ever.
      partialize: (s) => ({ drafts: s.drafts }) as unknown as State,
    },
  ),
);
