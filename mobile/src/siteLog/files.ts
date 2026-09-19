// SDK 54 moved the classic file API behind /legacy.
import * as FileSystem from 'expo-file-system/legacy';

/**
 * Keeping a picked file until the server has it.
 *
 * A picker hands back a URI in the OS cache. The system may reclaim that
 * file at any time - and for a recording it is a temporary file by
 * definition - so a capture that is waiting to be sent cannot rely on it.
 * Everything chosen for a capture is therefore COPIED into the app's own
 * document directory before the draft counts it as kept.
 *
 * Layout, one subtree per account and one per capture:
 *
 *     <documentDirectory>site-log/<user_id>/<capture_client_id>/<attachment_id><ext>
 *
 * Two things follow from that shape, both deliberate:
 *  - Accounts are isolated by path, so a device handoff can drop one
 *    account's files without touching another's.
 *  - Releasing a capture deletes exactly its own directory, so no file
 *    that another unsent draft still points at can be caught by it.
 *
 * Bytes and identity are unchanged by the copy: the same attachment id
 * names the file, the declaration is not touched, and the copied size is
 * verified against the size the picker reported before the copy is
 * accepted.
 *
 * WHAT IS STORED IS THE RELATIVE PATH, not the absolute URI. On iOS the
 * app container's absolute path can change - an OS update, a restore from
 * backup - while the Documents contents survive underneath it. A draft
 * that remembered the old absolute path would report a photo, or a
 * recording that cannot be made again, as missing while the bytes sat
 * there. Everything that uses a kept file resolves it through
 * `retainedUri` against the CURRENT directory.
 */

export type RetentionFailure =
  | 'unavailable'
  | 'copy_failed'
  | 'size_mismatch'
  | 'self_copy';

export class RetentionError extends Error {
  readonly cause: RetentionFailure;

  constructor(cause: RetentionFailure, message: string) {
    super(message);
    this.name = 'RetentionError';
    this.cause = cause;
  }
}

/** The app's own persistent area. Null on web, where there is none. */
function documentRoot(): string | null {
  return FileSystem.documentDirectory ?? null;
}

export function siteLogRoot(): string | null {
  const root = documentRoot();
  return root === null ? null : `${root}site-log/`;
}

export function userRoot(userId: string): string | null {
  const root = siteLogRoot();
  return root === null ? null : `${root}${userId}/`;
}

export function captureDir(userId: string, captureClientId: string): string | null {
  const root = userRoot(userId);
  return root === null ? null : `${root}${captureClientId}/`;
}

/** Where a kept file lives now, from the path that was recorded then. */
export function retainedUri(relativePath: string): string | null {
  const root = documentRoot();
  return root === null ? null : `${root}${relativePath}`;
}

/**
 * ONE canonical form for a file URI, or nothing.
 *
 * Everything downstream - ownership, identity, adopt-or-copy - compares
 * canonical forms. Comparing the strings as they arrive does not work:
 * `.../capture-1/./a.jpg`, `.../capture-1/a.jpg` and a percent-encoded
 * spelling are the same file, and `%2e%2e` is `..` once decoded. A rule
 * built from string prefixes needs a new special case for each of those;
 * one decode-and-normalise step needs none.
 *
 * Returns null for anything that cannot be decided - a non-file scheme,
 * malformed encoding, a path that climbs above the root. A caller that
 * gets null must REFUSE: not copy, not upload, not delete.
 */
export function canonicalFileUri(uri: string): string | null {
  if (typeof uri !== 'string') return null;
  const trimmed = uri.trim();
  if (!trimmed.toLowerCase().startsWith('file://')) return null;
  let rest = trimmed.slice('file://'.length);
  // file://localhost/path and file:///path both mean the local machine.
  if (rest.toLowerCase().startsWith('localhost/')) rest = rest.slice('localhost'.length);
  if (!rest.startsWith('/')) return null;
  // Strip a query or fragment: neither names a different file, and both
  // would otherwise hide the rest of the path from normalisation.
  rest = rest.split('?')[0].split('#')[0];

  let decoded: string;
  try {
    decoded = decodeURIComponent(rest);
  } catch {
    return null; // malformed encoding: undecidable
  }
  // A second pass changing anything means the input was double-encoded and
  // somebody is trying to smuggle a separator past one decode.
  try {
    if (decodeURIComponent(decoded) !== decoded) return null;
  } catch {
    return null;
  }
  if (decoded.includes('\0') || decoded.includes('\\')) return null;

  const out: string[] = [];
  for (const segment of decoded.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') {
      if (out.length === 0) return null; // climbs above the root
      out.pop();
      continue;
    }
    out.push(segment);
  }
  if (out.length === 0) return null;
  return `file:///${out.join('/')}`;
}

/** Do these two URIs name the same file? */
export function sameFile(a: string, b: string): boolean {
  const ca = canonicalFileUri(a);
  const cb = canonicalFileUri(b);
  return ca !== null && cb !== null && ca === cb;
}

/**
 * The path under the document directory this URI names, if it is a file
 * this account and this capture own.
 *
 * One file, directly inside `site-log/<user>/<capture>/`, decided on the
 * canonical form. Null for anything else, including anything undecidable.
 * The container may have moved since the URI was recorded, so the match is
 * made on the `site-log/...` tail rather than on the current absolute
 * prefix - which is exactly why the segments have to be normalised first.
 */
export function pathUnderDocuments(
  uri: string,
  owner: { userId: string; captureClientId: string },
): string | null {
  const canonical = canonicalFileUri(uri);
  if (canonical === null) return null;
  const segments = canonical.slice('file:///'.length).split('/');
  // Find OUR folder from the right: the tail below it is what survives a
  // container move.
  const at = segments.lastIndexOf('site-log');
  if (at < 0) return null;
  const tail = segments.slice(at);
  const relative = tail.join('/');
  return isOwnRetainedPath(relative, owner) ? relative : null;
}

/**
 * Is this recorded path one this capture is allowed to read?
 *
 * Applied to EVERY retained attachment, not only to the ones being
 * migrated: a stored path is persisted state, and persisted state is the
 * thing that can be wrong. One file, directly inside this account's and
 * this capture's own folder, decided after normalisation.
 */
export function isOwnRetainedPath(
  path: string,
  owner: { userId: string; captureClientId: string },
): boolean {
  // Normalised through the same door as everything else: a relative path
  // is checked as the URI it would resolve to.
  const canonical = canonicalFileUri(`file:///${path}`);
  if (canonical === null) return false;
  const relative = canonical.slice('file:///'.length);
  const prefix = `site-log/${owner.userId}/${owner.captureClientId}/`;
  if (!relative.startsWith(prefix)) return false;
  const rest = relative.slice(prefix.length);
  return rest.length > 0 && !rest.includes('/');
}

/**
 * What to do with one attachment's bytes: use them where they are, copy
 * them in, or refuse.
 *
 * The three cases the flow actually has, decided in one place:
 *
 *  - ALREADY OURS. The source is a file inside this capture's own folder -
 *    a draft this build wrote, or an older one whose absolute URI still
 *    resolves there. Adopt it. Never copy: with the source and the target
 *    naming the same file, the platform's copy deletes the destination
 *    first and the only copy is gone.
 *  - SOMEBODY ELSE'S. The source is inside the app's own area but belongs
 *    to another account or another capture, or it cannot be decided at
 *    all. Refuse. Copying it in would launder another capture's bytes into
 *    this one.
 *  - EXTERNAL. A picker's cache path, a recording's temporary file.
 *    Copy it in; that is what retention is for.
 */
export type RetentionPlan =
  | { action: 'adopt'; uri: string; path: string }
  | { action: 'copy'; from: string; to: string; path: string }
  | { action: 'refuse'; reason: 'undecidable' | 'not_ours' };

export function planRetention(args: {
  sourceUri: string;
  owner: { userId: string; captureClientId: string };
  attachmentId: string;
  name: string;
}): RetentionPlan {
  const dir = captureDir(args.owner.userId, args.owner.captureClientId);
  if (dir === null) return { action: 'refuse', reason: 'undecidable' };

  const fileName = `${args.attachmentId}${extensionOf(args.name)}`;
  const target = `${dir}${fileName}`;
  const canonicalTarget = canonicalFileUri(target);
  const canonicalSource = canonicalFileUri(args.sourceUri);
  if (canonicalTarget === null || canonicalSource === null) {
    return { action: 'refuse', reason: 'undecidable' };
  }

  // Already exactly where this attachment belongs.
  if (canonicalSource === canonicalTarget) {
    const path = retainedPath(args.owner.userId, args.owner.captureClientId, fileName);
    return { action: 'adopt', uri: target, path };
  }

  // Ours, under a different file name - an older draft, or a rename.
  const owned = pathUnderDocuments(args.sourceUri, args.owner);
  if (owned !== null) {
    const uri = retainedUri(owned);
    return uri === null
      ? { action: 'refuse', reason: 'undecidable' }
      : { action: 'adopt', uri, path: owned };
  }

  // Inside the app's own site-log area but not ours: never copy it in.
  const root = siteLogRoot();
  const canonicalRoot = root === null ? null : canonicalFileUri(root);
  if (canonicalRoot !== null && canonicalSource.startsWith(`${canonicalRoot}/`)) {
    return { action: 'refuse', reason: 'not_ours' };
  }

  return {
    action: 'copy',
    from: canonicalSource,
    to: target,
    path: retainedPath(args.owner.userId, args.owner.captureClientId, fileName),
  };
}

/** The path under the document directory, as stored in the draft. */
export function retainedPath(
  userId: string,
  captureClientId: string,
  fileName: string,
): string {
  return `site-log/${userId}/${captureClientId}/${fileName}`;
}

/** The extension of a picked file name, if it has a safe-looking one. */
function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  if (dot <= 0 || dot === name.length - 1) return '';
  const ext = name.slice(dot + 1);
  return /^[A-Za-z0-9]{1,8}$/.test(ext) ? `.${ext.toLowerCase()}` : '';
}

/**
 * Copy one picked file into the capture's own directory.
 *
 * Throws RetentionError rather than returning a partial success: a caller
 * that cannot keep the file must say so, never record the draft as if it
 * had. On any failure the partial copy is removed.
 */
export async function retainAttachment(args: {
  userId: string;
  captureClientId: string;
  attachmentId: string;
  sourceUri: string;
  name: string;
  /** What the picker said, when it said anything. Verified after the copy. */
  expectedSize: number | null;
}): Promise<{ uri: string; path: string; size: number | null }> {
  const dir = captureDir(args.userId, args.captureClientId);
  if (dir === null) {
    throw new RetentionError('unavailable', 'no document directory on this platform');
  }
  const fileName = `${args.attachmentId}${extensionOf(args.name)}`;
  const path = retainedPath(args.userId, args.captureClientId, fileName);
  const target = `${dir}${fileName}`;

  // Never copy a file onto itself. The platform's copy removes the
  // destination before writing, so a self-copy deletes the bytes and then
  // fails with nothing left. Callers decide through planRetention(); this
  // is the backstop for the one mistake that cannot be undone.
  if (sameFile(args.sourceUri, target)) {
    throw new RetentionError('self_copy', 'source and destination are the same file');
  }

  // What the copy must come out at. The picker's number when it gave one,
  // otherwise the source file's own - a recording never reports a size in
  // advance, and it is exactly the file that cannot be picked again.
  let expected = args.expectedSize;
  if (expected === null) {
    const from = await FileSystem.getInfoAsync(args.sourceUri);
    if (!from.exists) {
      throw new RetentionError('copy_failed', 'the file to keep is not there');
    }
    expected = typeof from.size === 'number' ? from.size : null;
  }
  try {
    await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
    await FileSystem.copyAsync({ from: args.sourceUri, to: target });
  } catch (err) {
    // Only ever the half-written destination. `sameFile` above guarantees
    // it is not the source, so cleanup cannot remove the only good copy.
    await deleteQuietly(target);
    throw new RetentionError('copy_failed', String(err));
  }

  // The legacy API returns `size` on an existing file; there is no option
  // to ask for it.
  const info = await FileSystem.getInfoAsync(target);
  if (!info.exists) {
    throw new RetentionError('copy_failed', 'the copy is not there afterwards');
  }
  const size = typeof info.size === 'number' ? info.size : null;
  // A short copy is the shape a full disk takes: copyAsync can return
  // without throwing and leave fewer bytes behind. "We could not measure
  // it" is not "it is fine" either - an unverified copy is not recorded as
  // kept, because the draft would then be claiming something nobody
  // checked.
  if (size === null || (expected !== null && size !== expected)) {
    await deleteQuietly(target);
    throw new RetentionError(
      'size_mismatch',
      size === null ? 'the copy could not be measured' : `kept ${size} bytes of ${expected}`,
    );
  }
  return { uri: target, path, size };
}

async function deleteQuietly(uri: string): Promise<void> {
  try {
    await FileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Best effort: a file that cannot be deleted is not a reason to fail
    // the operation that asked.
  }
}

/** Drop one attachment's kept copy - only ever one the caller owns. */
export async function releaseAttachment(uri: string): Promise<void> {
  const root = siteLogRoot();
  if (root === null || !uri.startsWith(root)) return;
  await deleteQuietly(uri);
}

/**
 * Drop everything kept for one capture.
 *
 * Called when the server has confirmed the capture saved, or when the user
 * discards it. Never called while a draft that points into this directory
 * is still waiting to be sent.
 */
export async function releaseCapture(
  userId: string,
  captureClientId: string,
): Promise<void> {
  const dir = captureDir(userId, captureClientId);
  if (dir === null) return;
  await deleteQuietly(dir);
}

/** Drop every kept file for every account on this device. */
export async function releaseAllRetained(): Promise<void> {
  const root = siteLogRoot();
  if (root === null) return;
  await deleteQuietly(root);
}

/** Is this file still where it was put? */
export async function fileExists(uri: string): Promise<boolean> {
  try {
    const info = await FileSystem.getInfoAsync(uri);
    return info.exists;
  } catch {
    return false;
  }
}
