import type { MediaType } from '../api/siteLog';

/**
 * The media class the SERVER will derive from a MIME type.
 *
 * A mirror of backend/app/services/evidence.py::derive_media_type, and it has
 * to stay one: `acquire_attachment` compares the class it derives from the
 * uploaded bytes' MIME against the class that was declared, and refuses the
 * upload when they differ. The declaration is pinned, so a wrong class is not
 * repairable by retrying - the attachment could never be saved at all.
 *
 * The inline-text row is reserved by ID, not by media class, so a file the
 * user picked may legitimately be declared `text` (a .txt or .csv note).
 */
export function deriveMediaType(mime: string): MediaType {
  const lowered = mime.toLowerCase();
  if (lowered.startsWith('audio/')) return 'audio';
  if (lowered.startsWith('image/')) return 'image';
  if (lowered.startsWith('text/')) return 'text';
  return 'document';
}
