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
  /**
   * What the next downloads do, one plan per call, in order. A call with
   * no plan throws, as before, so tests that never download are unchanged.
   */
  downloads: DownloadPlan[];
  /** Every downloadAsync call, as made: url, target and headers. */
  downloadCalls: { url: string; target: string; headers?: Record<string, string> }[];
  reset(): void;
  put(uri: string, size?: number): void;
};

export type DownloadPlan = {
  /** HTTP status the download reports. The body is written regardless, as
   *  the real one does - which is why the screen checks the status. */
  status: number;
  headers?: Record<string, string>;
  /** Bytes written to the target. */
  size?: number;
  /** Rejects instead of answering: a dropped connection. */
  error?: Error;
  /** Settled by the test, to hold the download open while it does
   *  something else - sign out, leave the screen. */
  hold?: Promise<void>;
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
  downloads: [],
  downloadCalls: [],
  reset() {
    memfs.files.clear();
    memfs.dirs.clear();
    memfs.failNextCopy = null;
    memfs.shortNextCopyTo = null;
    memfs.hideSizeOf = null;
    memfs.downloads = [];
    memfs.downloadCalls = [];
    state.documentDirectory = 'file:///documents/';
  },
  put(uri, size = 10) {
    memfs.files.set(uri, size);
  },
};

/**
 * The app's container, which iOS is free to move across an update or a
 * restore while its contents survive. Tests change it through
 * `setDocumentDirectory` to reproduce exactly that.
 */
const state = { documentDirectory: 'file:///documents/' };

export function setDocumentDirectory(next: string): void {
  state.documentDirectory = next;
}

export const cacheDirectory = 'file:///cache/';

// A getter, because production code reads FileSystem.documentDirectory at
// the moment it needs it - which is the behaviour being tested.
Object.defineProperty(exports, 'documentDirectory', {
  enumerable: true,
  get: () => state.documentDirectory,
});
export const documentDirectory: string = state.documentDirectory;

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
  // DESTRUCTIVE, like the installed expo-file-system on iOS: the
  // destination is removed before anything is written. A copy of a file
  // onto itself therefore deletes it and then fails with nothing to read.
  // Modelling this is the point - a mock where self-copy quietly succeeds
  // hides the one failure that loses a recording for good.
  memfs.files.delete(args.to);
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
  // UNDER it: `a/b` is under `a/`, `a/b.part` is not under `a/b`. Deleting
  // a file must not take its siblings that merely share a prefix.
  const under = uri.endsWith('/') ? uri : `${uri}/`;
  for (const key of [...memfs.files.keys()]) {
    if (key.startsWith(under)) memfs.files.delete(key);
  }
  for (const key of [...memfs.dirs]) {
    if (key.startsWith(under)) memfs.dirs.delete(key);
  }
}

export async function readDirectoryAsync(uri: string): Promise<string[]> {
  const prefix = uri.endsWith('/') ? uri : `${uri}/`;
  if (!memfs.dirs.has(prefix) && ![...memfs.files.keys()].some((k) => k.startsWith(prefix))) {
    throw new Error(`no such directory: ${uri}`);
  }
  const names = new Set<string>();
  for (const key of memfs.files.keys()) {
    if (!key.startsWith(prefix)) continue;
    const rest = key.slice(prefix.length);
    if (rest === '' || rest.includes('/')) continue; // this level only
    names.add(rest);
  }
  return [...names];
}

export async function downloadAsync(
  url: string,
  target: string,
  options?: { headers?: Record<string, string> },
): Promise<{ status: number; headers: Record<string, string>; uri: string }> {
  const plan = memfs.downloads.shift();
  if (!plan) throw new Error('not used by these tests');
  memfs.downloadCalls.push({ url, target, headers: options?.headers });
  if (plan.hold) await plan.hold;
  if (plan.error) throw plan.error;
  memfs.files.set(target, plan.size ?? 10);
  return { status: plan.status, headers: plan.headers ?? {}, uri: target };
}

export async function moveAsync(args: { from: string; to: string }): Promise<void> {
  const size = memfs.files.get(args.from);
  if (size === undefined) throw new Error(`no such file: ${args.from}`);
  memfs.files.delete(args.from);
  memfs.files.set(args.to, size);
}
