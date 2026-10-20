/**
 * The title a new task starts with, taken from its Site Log entry.
 *
 * The SAME rule the server applies (backend/app/services/tasks/rules.py,
 * `proposed_title_from`): the first line that is not blank, trimmed, cut to
 * 200 characters counted as code points, then trimmed again at the end. The
 * server derives the proposed title itself and records whether the saved
 * title differs from it; this copy only fills the field. If the two ever
 * disagree on some unusual whitespace, the cost is an audit row saying
 * "edited" for a title nobody touched - never a wrong task.
 */
export const TITLE_MAX_CHARS = 200;

const LINE_BREAK = /\r\n|\r|\n/;

export function proposedTitleFrom(body: string | null | undefined): string {
  if (!body) return '';
  for (const line of body.split(LINE_BREAK)) {
    const trimmed = line.trim();
    if (trimmed) {
      // Array.from walks code points: a slice of the string itself would
      // count UTF-16 units and could cut an emoji in half.
      return Array.from(trimmed).slice(0, TITLE_MAX_CHARS).join('').trimEnd();
    }
  }
  return '';
}

/** Characters as the server counts them (code points), after trimming. */
export function titleLength(title: string): number {
  return Array.from(title.trim()).length;
}

/** Whether a title can be saved: not blank, not over the limit. */
export function isUsableTitle(title: string): boolean {
  const n = titleLength(title);
  return n >= 1 && n <= TITLE_MAX_CHARS;
}
