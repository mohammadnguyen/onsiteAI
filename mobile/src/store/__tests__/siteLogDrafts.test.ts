jest.mock('expo-file-system/legacy', () =>
  require('../../siteLog/__tests__/support/memfs'),
);

import type { SiteLogDraft } from '../siteLogDrafts';

const STORAGE_KEY = 'site-log-drafts';

/**
 * A fresh module registry - what the app gets after a restart - together
 * with the storage and filesystem instances THAT registry sees.
 *
 * Reaching for the outer imports instead would inspect a different copy of
 * each mock: jest.resetModules gives the re-imported store new ones, and
 * the assertions would then be about a filesystem nothing wrote to.
 */
function freshStore() {
  jest.resetModules();
  /* eslint-disable @typescript-eslint/no-var-requires */
  const { useSiteLogDrafts } = require('../siteLogDrafts') as typeof import('../siteLogDrafts');
  const { memfs } = require('../../siteLog/__tests__/support/memfs') as typeof import('../../siteLog/__tests__/support/memfs');
  // The library's own jest mock is a CommonJS object, so it has no
  // `default`; the app's ESM import interops to the same object.
  const storageModule = require('@react-native-async-storage/async-storage');
  const AsyncStorage = (storageModule.default ??
    storageModule) as typeof import('@react-native-async-storage/async-storage').default;
  /* eslint-enable @typescript-eslint/no-var-requires */
  return { store: useSiteLogDrafts, memfs, AsyncStorage };
}

function makeDraft(over: Partial<SiteLogDraft> = {}): SiteLogDraft {
  return {
    capture_client_id: 'capture-1',
    user_id: 'user-a',
    created_at: 1,
    updated_at: 1,
    declaration: null,
    body_text: 'Rain stopped work at 2pm',
    job_id: null,
    attachments: [
      {
        attachment_client_id: 'att-1',
        media_type: 'image',
        uri: 'file:///documents/site-log/user-a/capture-1/att-1.jpg',
        name: 'att-1.jpg',
        mime: 'image/jpeg',
        size: 10,
        status: 'awaiting_upload',
        retained: true,
      },
    ],
    server: null,
    unconfirmed: false,
    last_message: null,
    ...over,
  };
}

beforeEach(async () => {
  const { memfs, AsyncStorage } = freshStore();
  memfs.reset();
  await AsyncStorage.clear();
});

describe('an unsent capture survives a restart', () => {
  it('is on disk before the request is sent, and loads again afterwards', async () => {
    const { store, memfs, AsyncStorage } = freshStore();
    const draft = makeDraft();
    memfs.put(draft.attachments[0].uri, 10);

    await store.getState().upsertDurable(draft);

    // Written, not merely scheduled.
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string).state.drafts[0].capture_client_id).toBe('capture-1');

    // Restart: a new module registry, reading the same storage.
    const restarted = freshStore();
    await restarted.store.persist.rehydrate();

    const loaded = restarted.store.getState().get('capture-1');
    expect(loaded?.body_text).toBe('Rain stopped work at 2pm');
    expect(loaded?.attachments[0].attachment_client_id).toBe('att-1');
    expect(loaded?.attachments[0].retained).toBe(true);
    // And the bytes are still there, because they were copied out of the
    // cache when the attachment was added.
    expect(restarted.memfs.files.has(loaded!.attachments[0].uri)).toBe(true);
  });
});

describe('per-account handling', () => {
  it('shows one account only its own drafts', async () => {
    const { store, memfs, AsyncStorage } = freshStore();
    await store.getState().upsertDurable(makeDraft());
    await store.getState().upsertDurable(
      makeDraft({ capture_client_id: 'capture-2', user_id: 'user-b' }),
    );

    expect(store.getState().forUser('user-a').map((d) => d.capture_client_id)).toEqual([
      'capture-1',
    ]);
    expect(store.getState().forUser('user-b').map((d) => d.capture_client_id)).toEqual([
      'capture-2',
    ]);
  });

  it('counts capacity per account, and refuses rather than evicting', async () => {
    const { store, memfs, AsyncStorage } = freshStore();
    for (let i = 0; i < 20; i += 1) {
      await store.getState().upsertDurable(
        makeDraft({ capture_client_id: `a-${i}`, user_id: 'user-a' }),
      );
    }
    expect(store.getState().atCapacity('user-a')).toBe(true);
    expect(store.getState().atCapacity('user-b')).toBe(false);
    // Nothing was dropped to make room for the last one.
    expect(store.getState().forUser('user-a')).toHaveLength(20);
  });
});

describe('releasing files', () => {
  it('deletes the files of the capture it removes, and no others', async () => {
    const { store, memfs, AsyncStorage } = freshStore();
    const first = makeDraft();
    const second = makeDraft({
      capture_client_id: 'capture-2',
      attachments: [
        {
          ...makeDraft().attachments[0],
          attachment_client_id: 'att-2',
          uri: 'file:///documents/site-log/user-a/capture-2/att-2.jpg',
        },
      ],
    });
    memfs.put(first.attachments[0].uri, 10);
    memfs.put(second.attachments[0].uri, 10);
    await store.getState().upsertDurable(first);
    await store.getState().upsertDurable(second);

    await store.getState().removeAndRelease('capture-1');

    expect(store.getState().get('capture-1')).toBeUndefined();
    expect(memfs.files.has(first.attachments[0].uri)).toBe(false);
    // The capture still waiting to be sent keeps its file.
    expect(store.getState().get('capture-2')).toBeDefined();
    expect(memfs.files.has(second.attachments[0].uri)).toBe(true);
  });

  it('removes the draft from storage, not only from memory', async () => {
    const { store, memfs, AsyncStorage } = freshStore();
    await store.getState().upsertDurable(makeDraft());
    await store.getState().removeAndRelease('capture-1');

    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    expect(JSON.parse(raw as string).state.drafts).toEqual([]);
  });
});
