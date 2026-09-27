import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import {
  isOwnKeptRecordingPath,
  listKeptRecordingFiles,
  releaseKeptRecording,
  retainedUri,
} from '../siteLog/files';

/**
 * Recordings kept because they were too large to attach.
 *
 * A voice note cannot be made again. When one exceeds the upload limit it
 * is refused as an attachment - an over-cap attachment would make the
 * whole capture undeclarable - but the bytes are kept, and the founder's
 * rule is that they stay until the user explicitly discards them.
 *
 * THE PROMISE COVERS WHAT WAS ACTUALLY PERSISTED, and nothing more. If
 * the copy into the account's area does not complete - no space, a write
 * error - the software does not claim the recording is safe: the user is
 * told it has NOT been saved reliably and that the recorder's temporary
 * file may be reclaimed. The source is never deleted, but a phone that
 * cannot write cannot be promised absolute preservation. Offering
 * playback or export of that temporary file is a recognised internal-test
 * limitation, deliberately not built in this round.
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
  /**
   * Make the list agree with the directory, which is the truth.
   *
   * The bytes are copied before the entry is written, so a process death
   * between the two would leave a preserved file that nothing lists -
   * unreachable, which is the one outcome this feature exists to
   * prevent. Anything found on disk without an entry gets one; any entry
   * whose file has gone is dropped, so the screen never offers a
   * recording that is not there.
   */
  reconcile: (userId: string) => Promise<void>;
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
          // The same canonical ownership rule the screen uses. A stored
          // path is persisted state: a traversal inside it would
          // otherwise let a discard delete another account's recording,
          // since the raw path still contains "/oversized/".
          if (!isOwnKeptRecordingPath(item.path, item.user_id)) return;
          const uri = retainedUri(item.path);
          if (uri !== null) await releaseKeptRecording(uri);
        }
      },
      forUser: (userId) => get().items.filter((x) => x.user_id === userId),
      reconcile: async (userId) => {
        const onDisk = await listKeptRecordingFiles(userId);
        const byPath = new Map(onDisk.map((f) => [f.path, f]));
        const mine = get().items.filter((x) => x.user_id === userId);
        const others = get().items.filter((x) => x.user_id !== userId);

        // Keep the entries whose file is still there, with the size the
        // disk reports rather than the one recorded earlier.
        const kept = mine
          .filter((x) => byPath.has(x.path))
          .map((x) => ({ ...x, size: byPath.get(x.path)!.size }));

        // Adopt anything on disk that no entry covers. The id is the
        // filename, which is how it was written, so adopting twice is
        // idempotent. The name is all that is lost, and the file keeps
        // its own.
        const known = new Set(kept.map((x) => x.path));
        const adopted: KeptRecordingEntry[] = onDisk
          .filter((f) => !known.has(f.path))
          .map((f) => {
            const filename = f.path.slice(f.path.lastIndexOf('/') + 1);
            return {
              id: filename.replace(/\.[^.]+$/, ''),
              user_id: userId,
              name: filename,
              path: f.path,
              size: f.size,
              created_at: 0, // unknown; it was not this run that wrote it
              capture_client_id: '',
            };
          });

        if (adopted.length === 0 && kept.length === mine.length) return;
        set({ items: [...adopted, ...kept, ...others] });
        await flush();
      },
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (s) => ({ items: s.items }) as unknown as State,
    },
  ),
);
