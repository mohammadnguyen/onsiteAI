/**
 * The potential-variation wording the founder fixed (2026-11-08), in both
 * languages, and the PV section's key parity.
 *
 * - A lookup that found nothing says "not found yet", never "was not saved":
 *   the first request may still commit.
 * - While the server offers no flags, nothing runs in the background: the
 *   words ask the user to come back and tap the check button, by its own
 *   label, and promise no automatic recovery.
 * - The pinned text is "the entry as confirmed when flagged", not "the
 *   original record".
 */
import en from '../../i18n/en.json';
import zh from '../../i18n/zh.json';

type Tree = { [k: string]: string | Tree };

function keys(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === 'string' ? [`${prefix}${k}`] : keys(v, `${prefix}${k}.`),
  );
}

const EN = en.potentialVariations;
const ZH = zh.potentialVariations;

describe('potential-variation copy', () => {
  it('has the same keys in English and Chinese', () => {
    expect(keys(ZH as Tree).sort()).toEqual(keys(EN as Tree).sort());
  });

  it('a lookup that found nothing says "not found yet", not "not saved"', () => {
    expect(ZH.pending.not_found_title).toBe('暂未查到这条标记');
    expect(EN.pending.not_found_title).toBe('This flag has not been found yet');
    expect(EN.pending.not_found_title.toLowerCase()).not.toContain('not saved');
  });

  it('while flags are unavailable, asks the user to come back and tap the check button by its label', () => {
    expect(ZH.pending.check_again).toBe('再次确认');
    expect(EN.pending.check_again).toBe('Check again');
    for (const text of [ZH.pending.unavailable_body, ZH.pending.kept_unavailable]) {
      expect(text).toContain(`“${ZH.pending.check_again}”`);
      // No promise that it happens by itself once the feature is back.
      expect(text).not.toMatch(/恢复后会再次确认/);
    }
    for (const text of [EN.pending.unavailable_body, EN.pending.kept_unavailable]) {
      expect(text).toContain(`"${EN.pending.check_again}"`);
      expect(text).not.toMatch(/checked again once/i);
    }
    expect(ZH.pending.unavailable_body).toContain('不会在后台自动');
    expect(EN.pending.unavailable_body).toContain('Nothing is checked or sent in the background');
  });

  it('names the pinned text as the entry confirmed when flagging', () => {
    expect(ZH.detail.original).toBe('标记时确认的记录：第 {{n}} 版，记录于 {{time}}');
    expect(EN.detail.original).toBe('Entry as confirmed when flagged: version {{n}}, recorded {{time}}');
  });

  it('says why a save was refused when the entry’s job changed, and asks why a flag is moved', () => {
    expect(ZH.new.job_changed).toContain('项目');
    expect(EN.new.job_changed).toContain('job');
    expect(ZH.detail.job_move_reason_placeholder).toBe('原因（必填）');
    expect(EN.detail.job_move_reason_placeholder).toBe('Reason (required)');
  });
});
