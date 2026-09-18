import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { AttachmentState, CaptureStatus, Declaration, MediaType } from '../api/siteLog';

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
 *  - ATTACHMENT FILES ARE NOT COPIED HERE. A draft stores the `uri` the picker
 *    returned. Those live in the OS cache and CAN be reclaimed — by the system,
 *    or by the app being reinstalled. A resumed draft therefore verifies each
 *    file still exists and marks the ones that do not as `missing`, rather than
 *    failing an upload later with something unexplainable.
 *  - Bounded: at most MAX_DRAFTS, newest first.
 *  - Never tokens, never credentials, never raw response payloads.
 */

export type DraftAttachmentStatus = AttachmentState | 'missing';

export type DraftAttachment = {
  attachment_client_id: string;
  media_type: Exclude<MediaType, 'text'>;
  uri: string;
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
  patch: (captureClientId: string, p: Partial<SiteLogDraft>) => void;
  remove: (captureClientId: string) => void;
  forUser: (userId: string) => SiteLogDraft[];
  get: (captureClientId: string) => SiteLogDraft | undefined;
  clearAll: () => void;
};

const MAX_DRAFTS = 20;

export const useSiteLogDrafts = create<State>()(
  persist(
    (set, get) => ({
      drafts: [],
      upsert: (d) =>
        set((s) => ({
          drafts: [d, ...s.drafts.filter((x) => x.capture_client_id !== d.capture_client_id)]
            .slice(0, MAX_DRAFTS),
        })),
      patch: (id, p) =>
        set((s) => ({
          drafts: s.drafts.map((x) =>
            x.capture_client_id === id ? { ...x, ...p, updated_at: Date.now() } : x,
          ),
        })),
      remove: (id) =>
        set((s) => ({ drafts: s.drafts.filter((x) => x.capture_client_id !== id) })),
      forUser: (userId) => get().drafts.filter((d) => d.user_id === userId),
      get: (id) => get().drafts.find((d) => d.capture_client_id === id),
      clearAll: () => set({ drafts: [] }),
    }),
    { name: 'site-log-drafts', storage: createJSONStorage(() => AsyncStorage) },
  ),
);
