/**
 * What a saved record holds, counted by kind - for the list row.
 *
 * Forty-seven records that all read "(no text)" are forty-seven records
 * the user cannot tell apart. A record with no body still has a shape:
 * one photo, or a drawing and a voice note. The row says that shape.
 *
 * Pure: no rendering, no translation. The screen turns the counts into
 * words, so the order and the exclusion below are testable on their own.
 */
import type { AttachmentOut, MediaType } from '../api/siteLog';

export type MediaCount = { type: MediaType; count: number };

/** The order kinds are listed in: what the eye wants first on site. */
const ORDER: MediaType[] = ['image', 'document', 'audio', 'text'];

/**
 * Attachments the user attached, grouped by declared kind, in display
 * order, zero counts dropped.
 *
 * The server's own copy of the body text is not counted: the body is
 * already the first line of the row, and counting it as "1 text file"
 * says the user attached a file they did not. Only an explicit `true`
 * excludes it - an older backend that sends no flag hides nothing, and a
 * .txt the user really attached is counted as the text file it is.
 */
export function mediaCounts(attachments: readonly AttachmentOut[]): MediaCount[] {
  const tally = new Map<MediaType, number>();
  for (const a of attachments) {
    if (a.is_inline_text === true) continue;
    tally.set(a.declared_media_type, (tally.get(a.declared_media_type) ?? 0) + 1);
  }
  return ORDER.filter((type) => (tally.get(type) ?? 0) > 0).map((type) => ({
    type,
    count: tally.get(type) ?? 0,
  }));
}

/**
 * The counts as one line, using the caller's translator so the words
 * come from the same place as every other string on the screen.
 * `null` when there is nothing to say.
 */
export function describeMedia(
  attachments: readonly AttachmentOut[],
  t: (key: string, opts: { count: number }) => string,
): string | null {
  const parts = mediaCounts(attachments).map(({ type, count }) =>
    t(`siteLog.list.count_${type}`, { count }),
  );
  return parts.length > 0 ? parts.join(' · ') : null;
}
