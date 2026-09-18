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

export type RetentionFailure = 'unavailable' | 'copy_failed' | 'size_mismatch';

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
 * The path under the document directory that an older absolute URI meant.
 *
 * Drafts written before the relative path existed hold a full
 * `file:///.../Documents/site-log/...` URI. Two cases, both recoverable:
 * the container has not moved, so the URI still starts with the current
 * document directory; or it has, and the tail from our own folder onwards
 * is still correct relative to wherever Documents is now.
 *
 * Returns null for anything that was never ours - a picker's cache path,
 * for instance, which has to be copied in rather than adopted.
 */
export function pathUnderDocuments(uri: string): string | null {
  const root = documentRoot();
  if (root !== null && uri.startsWith(root)) return uri.slice(root.length);
  const marker = 'site-log/';
  const at = uri.lastIndexOf(marker);
  return at >= 0 ? uri.slice(at) : null;
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
