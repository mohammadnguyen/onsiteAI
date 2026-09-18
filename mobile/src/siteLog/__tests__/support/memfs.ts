/**
 * An in-memory stand-in for expo-file-system/legacy.
 *
 * Enough of the surface for the retention code: a directory tree, copies
 * that can be made to fail or to fall short, and deletes. Tests reach in
 * through `memfs` to plant files, break a copy, or inspect what is left.
 */
export type MemFs = {
  files: Map<string, number>;
  dirs: Set<string>;
  /** Next copy fails outright. */
  failNextCopy: Error | null;
  /** Next copy writes this many bytes instead of the source's size. */
  shortNextCopyTo: number | null;
  /** getInfoAsync reports no size for this path, as some platforms do. */
  hideSizeOf: string | null;
  reset(): void;
  put(uri: string, size?: number): void;
};

/**
 * The backing store lives on globalThis so it survives jest.resetModules(),
 * which is how these tests express "the app restarted". Files written before
 * a restart must still be there afterwards - that is the whole point of
 * copying them out of the cache.
 */
const g = globalThis as unknown as {
  __memfsFiles?: Map<string, number>;
  __memfsDirs?: Set<string>;
};
g.__memfsFiles = g.__memfsFiles ?? new Map<string, number>();
g.__memfsDirs = g.__memfsDirs ?? new Set<string>();

export const memfs: MemFs = {
  files: g.__memfsFiles,
  dirs: g.__memfsDirs,
  failNextCopy: null,
  shortNextCopyTo: null,
  hideSizeOf: null,
  reset() {
    memfs.files.clear();
    memfs.dirs.clear();
    memfs.failNextCopy = null;
    memfs.shortNextCopyTo = null;
    memfs.hideSizeOf = null;
  },
  put(uri, size = 10) {
    memfs.files.set(uri, size);
  },
};

export const documentDirectory = 'file:///documents/';
export const cacheDirectory = 'file:///cache/';

export async function makeDirectoryAsync(
  uri: string,
  _options?: { intermediates?: boolean },
): Promise<void> {
  memfs.dirs.add(uri);
}

export async function copyAsync(args: { from: string; to: string }): Promise<void> {
  if (memfs.failNextCopy) {
    const err = memfs.failNextCopy;
    memfs.failNextCopy = null;
    throw err;
  }
  const source = memfs.files.get(args.from);
  if (source === undefined) throw new Error(`no such file: ${args.from}`);
  const written = memfs.shortNextCopyTo ?? source;
  memfs.shortNextCopyTo = null;
  memfs.files.set(args.to, written);
}

export async function getInfoAsync(
  uri: string,
): Promise<{ exists: boolean; size?: number; uri: string }> {
  const size = memfs.files.get(uri);
  if (size !== undefined) {
    return memfs.hideSizeOf === uri
      ? { exists: true, uri }
      : { exists: true, size, uri };
  }
  if (memfs.dirs.has(uri)) return { exists: true, uri };
  return { exists: false, uri };
}

export async function deleteAsync(
  uri: string,
  _options?: { idempotent?: boolean },
): Promise<void> {
  memfs.files.delete(uri);
  memfs.dirs.delete(uri);
  // A directory delete takes everything under it, as the real one does.
  for (const key of [...memfs.files.keys()]) {
    if (key.startsWith(uri)) memfs.files.delete(key);
  }
  for (const key of [...memfs.dirs]) {
    if (key.startsWith(uri)) memfs.dirs.delete(key);
  }
}

export async function downloadAsync(): Promise<never> {
  throw new Error('not used by these tests');
}

export async function moveAsync(args: { from: string; to: string }): Promise<void> {
  const size = memfs.files.get(args.from);
  if (size === undefined) throw new Error(`no such file: ${args.from}`);
  memfs.files.delete(args.from);
  memfs.files.set(args.to, size);
}
