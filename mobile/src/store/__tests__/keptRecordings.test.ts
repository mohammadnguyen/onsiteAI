jest.mock('expo-file-system/legacy', () =>
  require('../../siteLog/__tests__/support/memfs'),
);

/**
 * Recordings kept because they were too large to attach.
 *
 * The rule, in the founder's words: a refused recording must survive
 * until the user explicitly discards it. A failed preservation must not
 * delete anything, and no page cleanup may remove it indirectly.
 */

const STORAGE_KEY = 'site-log-kept-recordings';

function fresh() {
  jest.resetModules();
  /* eslint-disable @typescript-eslint/no-var-requires */
  const files = require('../../siteLog/files') as typeof import('../../siteLog/files');
  const { useKeptRecordings } =
    require('../keptRecordings') as typeof import('../keptRecordings');
  const { useSiteLogDrafts } =
    require('../siteLogDrafts') as typeof import('../siteLogDrafts');
  const { memfs } = require('../../siteLog/__tests__/support/memfs') as typeof import('../../siteLog/__tests__/support/memfs');
  const storageModule = require('@react-native-async-storage/async-storage');
  const AsyncStorage = (storageModule.default ??
    storageModule) as typeof import('@react-native-async-storage/async-storage').default;
  /* eslint-enable @typescript-eslint/no-var-requires */
  return { files, useKeptRecordings, useSiteLogDrafts, memfs, AsyncStorage };
}

async function clean() {
  const mod = fresh();
  await new Promise((r) => setTimeout(r, 0));
  mod.memfs.reset();
  await mod.AsyncStorage.clear();
  mod.useKeptRecordings.setState({ items: [] });
  mod.useSiteLogDrafts.setState({ drafts: [], submitting: [] });
  return mod;
}

const SOURCE = 'file:///cache/voice-1.m4a';

describe('keepOversizedRecording', () => {
  it('copies into the account area and leaves the source alone', async () => {
    const { files, memfs } = await clean();
    memfs.put(SOURCE, 60 * 1024 * 1024);

    const kept = await files.keepOversizedRecording({
      userId: 'user-a',
      attachmentId: 'att-1',
      sourceUri: SOURCE,
      name: 'voice-1.m4a',
      expectedSize: 60 * 1024 * 1024,
    });

    expect(kept.path).toBe('site-log/user-a/oversized/att-1.m4a');
    expect(memfs.files.has(kept.uri)).toBe(true);
    // A COPY. The earlier version moved, and deleted the source when the
    // move failed - so the one path where preservation did not work was
    // also the path that destroyed the recording.
    expect(memfs.files.has(SOURCE)).toBe(true);
  });

  it('deletes nothing when it cannot keep the file', async () => {
    const { files, memfs } = await clean();
    memfs.put(SOURCE, 60 * 1024 * 1024);
    memfs.failNextCopy = new Error('no space');

    await expect(
      files.keepOversizedRecording({
        userId: 'user-a',
        attachmentId: 'att-1',
        sourceUri: SOURCE,
        name: 'voice-1.m4a',
        expectedSize: 60 * 1024 * 1024,
      }),
    ).rejects.toThrow();

    // THE point of this test: a failure costs nothing.
    expect(memfs.files.has(SOURCE)).toBe(true);
    expect([...memfs.files.keys()].filter((p) => p.includes('/oversized/'))).toEqual([]);
  });

  it('REGRESSION: refuses a copy that arrived short, without deleting the source', async () => {
    // A short copy does not throw - it returns having written fewer
    // bytes. Checking only "more than zero" accepted 1 KiB of a 60 MiB
    // recording and announced it as kept; once the recorder's temporary
    // file is reclaimed the rest is gone for good.
    const { files, memfs } = await clean();
    memfs.put(SOURCE, 60 * 1024 * 1024);
    memfs.shortNextCopyTo = 1024;

    await expect(
      files.keepOversizedRecording({
        userId: 'user-a',
        attachmentId: 'att-1',
        sourceUri: SOURCE,
        name: 'voice-1.m4a',
        expectedSize: 60 * 1024 * 1024,
      }),
    ).rejects.toThrow(/kept 1024 bytes of 62914560/);

    expect(memfs.files.has(SOURCE)).toBe(true);
    // The truncated destination is not left behind to be adopted later.
    expect([...memfs.files.keys()].filter((p) => p.includes('/oversized/'))).toEqual([]);
  });

  it('is not reachable outside the app area, and refuses a self-copy', async () => {
    const { files, memfs } = await clean();
    const target = 'file:///documents/site-log/user-a/oversized/att-1.m4a';
    memfs.put(target, 10);
    await expect(
      files.keepOversizedRecording({
        userId: 'user-a',
        attachmentId: 'att-1',
        sourceUri: target,
        name: 'voice-1.m4a',
        expectedSize: 10,
      }),
    ).rejects.toThrow();
    expect(memfs.files.has(target)).toBe(true);
  });
});

describe('a kept recording survives everything except an explicit discard', () => {
  async function keptOne() {
    const mod = await clean();
    mod.memfs.put(SOURCE, 60 * 1024 * 1024);
    const kept = await mod.files.keepOversizedRecording({
      userId: 'user-a',
      attachmentId: 'att-1',
      sourceUri: SOURCE,
      name: 'voice-1.m4a',
      expectedSize: 60 * 1024 * 1024,
    });
    await mod.useKeptRecordings.getState().add({
      id: 'att-1',
      user_id: 'user-a',
      name: 'voice-1.m4a',
      path: kept.path,
      size: kept.size,
      created_at: 1,
      capture_client_id: 'cap-1',
    });
    return { ...mod, kept };
  }

  it('survives releasing the capture it was refused from', async () => {
    const { files, memfs, kept } = await keptOne();
    memfs.put('file:///documents/site-log/user-a/cap-1/att.jpg', 10);

    await files.releaseCapture('user-a', 'cap-1');

    expect(memfs.files.has('file:///documents/site-log/user-a/cap-1/att.jpg')).toBe(false);
    expect(memfs.files.has(kept.uri)).toBe(true);
  });

  it('survives logout, which clears that account\'s captures', async () => {
    const { useSiteLogDrafts, memfs, kept } = await keptOne();
    memfs.put('file:///documents/site-log/user-a/cap-1/att.jpg', 10);

    await useSiteLogDrafts.getState().clearCaptures('user-a', ['cap-1']);

    expect(memfs.files.has(kept.uri)).toBe(true);
  });

  it('survives a restart - the index is on disk, not in memory', async () => {
    const { AsyncStorage, kept } = await keptOne();

    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    const persisted = JSON.parse(raw ?? '{}') as {
      state: { items: { path: string }[] };
    };
    expect(persisted.state.items.map((i) => i.path)).toEqual([kept.path]);
  });

  it('is removed, file and entry, ONLY by an explicit discard', async () => {
    const { useKeptRecordings, memfs, kept } = await keptOne();

    await useKeptRecordings.getState().discard('att-1');

    expect(useKeptRecordings.getState().forUser('user-a')).toHaveLength(0);
    expect(memfs.files.has(kept.uri)).toBe(false);
  });

  it('REGRESSION: a file preserved before the index was written is adopted', async () => {
    // The copy happens before the entry. A process death between the two
    // used to leave a preserved recording that nothing listed - which is
    // the one outcome this feature exists to prevent.
    const { files, useKeptRecordings, memfs } = await clean();
    memfs.put(SOURCE, 60 * 1024 * 1024);
    const kept = await files.keepOversizedRecording({
      userId: 'user-a',
      attachmentId: 'att-orphan',
      sourceUri: SOURCE,
      name: 'voice-1.m4a',
      expectedSize: 60 * 1024 * 1024,
    });
    // ...and the app dies here, before `add`.
    expect(useKeptRecordings.getState().forUser('user-a')).toHaveLength(0);

    await useKeptRecordings.getState().reconcile('user-a');

    const found = useKeptRecordings.getState().forUser('user-a');
    expect(found).toHaveLength(1);
    expect(found[0].path).toBe(kept.path);
    expect(found[0].size).toBe(60 * 1024 * 1024);
  });

  it('REGRESSION: an entry whose file has gone is dropped', async () => {
    // The opposite drift: the screen must never offer a recording that
    // is not there.
    const { useKeptRecordings } = await keptOne();
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { memfs } = require('../../siteLog/__tests__/support/memfs') as typeof import('../../siteLog/__tests__/support/memfs');
    memfs.files.clear();

    await useKeptRecordings.getState().reconcile('user-a');

    expect(useKeptRecordings.getState().forUser('user-a')).toHaveLength(0);
  });

  it('reconciling leaves another account alone', async () => {
    const { useKeptRecordings } = await keptOne();
    await useKeptRecordings.getState().add({
      id: 'att-b',
      user_id: 'user-b',
      name: 'other.m4a',
      path: 'site-log/user-b/oversized/att-b.m4a',
      size: 5,
      created_at: 2,
      capture_client_id: 'cap-b',
    });

    await useKeptRecordings.getState().reconcile('user-a');

    // user-b has no files on disk here, but reconciling user-a must not
    // touch their list.
    expect(useKeptRecordings.getState().forUser('user-b')).toHaveLength(1);
  });

  it('shows each account only its own', async () => {
    const { useKeptRecordings } = await keptOne();
    await useKeptRecordings.getState().add({
      id: 'att-2',
      user_id: 'user-b',
      name: 'other.m4a',
      path: 'site-log/user-b/oversized/att-2.m4a',
      size: 1,
      created_at: 2,
      capture_client_id: 'cap-2',
    });

    expect(useKeptRecordings.getState().forUser('user-a').map((i) => i.id)).toEqual(['att-1']);
    expect(useKeptRecordings.getState().forUser('user-b').map((i) => i.id)).toEqual(['att-2']);
  });
});
