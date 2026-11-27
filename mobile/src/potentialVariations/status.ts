import type { PvAttachment, PvOut, PvReviewState } from '../api/potentialVariations';

/**
 * The flag's title, only when this user may read its source entry - the
 * title was taken from the entry's text. Null otherwise: the caller shows a
 * neutral placeholder, never anything derived from the entry. The server
 * already sends no title then; this keeps the screens to the same rule.
 */
export function visibleTitle(pv: Pick<PvOut, 'title' | 'source'>): string | null {
  return pv.source.readable ? pv.title : null;
}

/**
 * Which badge style a review state uses (src/ui/kit StatusBadge keys).
 *
 * Colour discipline from the design tokens: amber only for something that
 * needs attention, green only for done/healthy. "To check" is the state
 * that asks for attention. "Follow-up ended" is NOT green: ending follow-up
 * is no approval and no settlement, so it stays neutral, like "Following
 * up".
 */
export function pvStateBadgeKey(state: PvReviewState): string {
  switch (state) {
    case 'to_check':
      return 'pending';
    case 'ended':
      return 'completed';
    default:
      return state; // neutral fallback style
  }
}

/** Shown in place of a count once a capped page is full: "50+". */
export function cappedCount(count: number, cap: number): string {
  return count >= cap ? `${cap}+` : String(count);
}

type MediaType = PvAttachment['declared_media_type'];
const MEDIA_ORDER: readonly MediaType[] = ['image', 'document', 'audio', 'text'];

/**
 * The entry's CURRENT attachments counted by kind, in a fixed order -
 * e.g. [['image', 2], ['audio', 1]]. Kinds with none are left out.
 */
export function attachmentCounts(list: readonly PvAttachment[]): [MediaType, number][] {
  return MEDIA_ORDER.map((m): [MediaType, number] => [
    m,
    list.filter((a) => a.declared_media_type === m).length,
  ]).filter(([, n]) => n > 0);
}
