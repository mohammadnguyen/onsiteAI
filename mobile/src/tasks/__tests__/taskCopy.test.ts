/**
 * The task create's recovery wording, in both languages, held to the rules
 * the founder fixed for the potential-variation copy of this flow
 * (2026-11-08), and the task section's key parity.
 *
 * - A lookup that found nothing says "not found yet", never "was not saved":
 *   the first request may still commit.
 * - While the server offers no tasks, nothing runs in the background: the
 *   words ask the user to come back and tap the check button, by its own
 *   label, and promise no automatic re-check.
 */
import en from '../../i18n/en.json';
import zh from '../../i18n/zh.json';

type Tree = { [k: string]: string | Tree };

function keys(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === 'string' ? [`${prefix}${k}`] : keys(v, `${prefix}${k}.`),
  );
}

const EN = en.tasks;
const ZH = zh.tasks;

describe('task create recovery copy', () => {
  it('has the same keys in English and Chinese', () => {
    expect(keys(ZH as Tree).sort()).toEqual(keys(EN as Tree).sort());
  });

  it('a lookup that found nothing says "not found yet", not "not saved"', () => {
    expect(ZH.pending.not_found_title).toBe('暂未查到这条待办');
    expect(EN.pending.not_found_title).toBe('This task has not been found yet');
    expect(EN.pending.not_found_title.toLowerCase()).not.toContain('not saved');
    expect(ZH.pending.not_found_title).not.toContain('没有保存');
  });

  it('while tasks are unavailable, asks the user to come back and tap the check button by its label', () => {
    expect(ZH.pending.check_again).toBe('再次确认');
    expect(EN.pending.check_again).toBe('Check again');
    for (const text of [ZH.pending.unavailable_body, ZH.pending.kept_unavailable]) {
      expect(text).toContain(`“${ZH.pending.check_again}”`);
      // No promise that it happens by itself once tasks are back.
      expect(text).not.toMatch(/恢复后会再次确认/);
    }
    for (const text of [EN.pending.unavailable_body, EN.pending.kept_unavailable]) {
      expect(text).toContain(`"${EN.pending.check_again}"`);
      expect(text).not.toMatch(/checked again once/i);
    }
    expect(ZH.pending.unavailable_body).toContain('不会在后台自动');
    expect(EN.pending.unavailable_body).toContain('Nothing is checked or sent in the background');
  });
});
