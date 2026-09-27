/**
 * Is a URL the one local document the viewer was opened on?
 *
 * The in-app PDF viewer is a web view, and a web view will load whatever
 * a link inside the document points at unless every request is checked.
 * This is the DECISION of that check, pure and tested on its own: ONLY the
 * opened file, by its full canonical path - not by its name (a same-named
 * file in another directory is a different file), not by prefix (`..` is
 * refused, never resolved), and not by scheme alone.
 *
 * It is not the whole fence. It only decides what it is ASKED about: the
 * web view must be configured so that every request reaches it
 * (`originWhitelist={['*']}` - see the comment at the WebView in
 * app/site-log/[id].tsx for why a narrower whitelist opens Safari), read
 * access must be the document itself, and link previews must be off.
 *
 * Canonical means: the file scheme, an absolute path, percent-decoding
 * undone, `.` and empty segments dropped, and iOS's `/private/var/...`
 * alias folded onto `/var/...` - WKWebView may report the same file under
 * either. Anything that does not canonicalise is refused.
 */

const SCHEME = 'file://';

/** The canonical absolute path of a file URL, or null if it is not one. */
export function canonicalLocalPath(uri: string): string | null {
  if (!uri.startsWith(SCHEME)) return null;
  // Query and fragment are not part of the file.
  let rest = uri.slice(SCHEME.length).split(/[?#]/)[0];
  // `file://localhost/x` is `file:///x`. Any other authority is refused.
  if (rest.startsWith('localhost/')) rest = rest.slice('localhost'.length);
  if (!rest.startsWith('/')) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rest);
  } catch {
    return null; // malformed escape: not a path this app wrote
  }
  if (decoded.includes('\0')) return null;
  const segments: string[] = [];
  for (const seg of decoded.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') return null; // traversal is refused, not resolved
    segments.push(seg);
  }
  let path = `/${segments.join('/')}`;
  if (path.startsWith('/private/var/')) path = path.slice('/private'.length);
  return path;
}

/** True only when both are file URLs naming the same canonical path. */
export function isSameLocalFile(requested: string, selected: string): boolean {
  const a = canonicalLocalPath(requested);
  const b = canonicalLocalPath(selected);
  return a !== null && b !== null && a === b;
}
