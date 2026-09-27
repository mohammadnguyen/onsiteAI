import type { AttachmentOut } from '../../api/siteLog';
import { describeMedia, mediaCounts } from '../summary';

function att(
  type: AttachmentOut['declared_media_type'],
  extra: Partial<AttachmentOut> = {},
): AttachmentOut {
  return {
    attachment_client_id: `${type}-${Math.random()}`,
    declared_media_type: type,
    declared_size_bytes: 1,
    state: 'stored',
    evidence_id: 'ev',
    ...extra,
  };
}

const t = (key: string, opts: { count: number }) => `${key}:${opts.count}`;

describe('mediaCounts', () => {
  it('counts each kind the user attached, in display order', () => {
    const counts = mediaCounts([att('audio'), att('document'), att('image'), att('image')]);
    expect(counts).toEqual([
      { type: 'image', count: 2 },
      { type: 'document', count: 1 },
      { type: 'audio', count: 1 },
    ]);
  });

  it('does not count the server-marked inline text row', () => {
    expect(mediaCounts([att('text', { is_inline_text: true }), att('image')])).toEqual([
      { type: 'image', count: 1 },
    ]);
  });

  it('counts a text file the user attached: absent flag means not known, not inline', () => {
    // An older backend sends no flag at all. Hiding on type would make a
    // real .txt attachment disappear from the count.
    expect(mediaCounts([att('text'), att('text', { is_inline_text: false })])).toEqual([
      { type: 'text', count: 2 },
    ]);
  });

  it('is empty for a record with nothing attached', () => {
    expect(mediaCounts([])).toEqual([]);
    expect(mediaCounts([att('text', { is_inline_text: true })])).toEqual([]);
  });
});

describe('describeMedia', () => {
  it('joins the counts through the translator', () => {
    expect(describeMedia([att('image'), att('document'), att('document')], t)).toBe(
      'siteLog.list.count_image:1 · siteLog.list.count_document:2',
    );
  });

  it('says nothing when there is nothing to say', () => {
    expect(describeMedia([], t)).toBeNull();
    expect(describeMedia([att('text', { is_inline_text: true })], t)).toBeNull();
  });
});
