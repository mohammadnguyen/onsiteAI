import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { releaseKeptRecording, retainedUri } from '../siteLog/files';

/**
 * Recordings kept because they were too large to attach.
 *
 * A voice note cannot be made again. When one exceeds the upload limit it
 * is refused as an attachment - an over-cap attachment would make the
 * whole capture undeclarable - but the bytes are kept, and the founder's
 * rule is that they stay until the user explicitly discards them.
 *
 * This is the index that makes them reachable. Without it the files would
 * sit in a directory nobody can open, which is emergency preservation and
 * not a recovery path.
 *
 * Per account, like drafts: the list only ever shows the signed-in user's
 * own, and nothing here is touched by capture cleanup.
 *
 * PATH, NOT URI, for the same reason drafts store a path: iOS can move the
 * app container across an update or a restore while the Documents contents
 * survive, and an absolute URI recorded before that points nowhere
 * afterwards.
 */
export type KeptRecordingEntry = {
  id: string;
  user_id: string;
  /** What the recorder called it, for display. */
  name: string;
  /** Relative to the document directory. */
  path: string;
  size: number;
  created_at: number;
  /** The capture it was refused from, for context only. */
  capture_client_id: string;
};

type State = {
  items: KeptRecordingEntry[];
  add: (entry: KeptRecordingEntry) => Promise<void>;
  /** Forget one AND delete its file. Only ever on an explicit discard. */
  discard: (id: string) => Promise<void>;
  forUser: (userId: string) => KeptRecordingEntry[];
};

const STORAGE_KEY = 'site-log-kept-recordings';

async function flush(): Promise<void> {
  const state = { items: useKeptRecordings.getState().items };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

export const useKeptRecordings = create<State>()(
  persist(
    (set, get) => ({
      items: [],
      add: async (entry) => {
        set((s) => ({ items: [entry, ...s.items.filter((x) => x.id !== entry.id)] }));
        // Awaited: the file is already on disk, and an index that has not
        // been written yet is how a kept recording becomes unreachable.
        await flush();
      },
      discard: async (id) => {
        const item = get().items.find((x) => x.id === id);
        set((s) => ({ items: s.items.filter((x) => x.id !== id) }));
        await flush();
        if (item) {
          const uri = retainedUri(item.path);
          if (uri !== null) await releaseKeptRecording(uri);
        }
      },
      forUser: (userId) => get().items.filter((x) => x.user_id === userId),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (s) => ({ items: s.items }) as unknown as State,
    },
  ),
);
