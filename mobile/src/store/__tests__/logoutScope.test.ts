jest.mock('expo-file-system/legacy', () =>
  require('../../siteLog/__tests__/support/memfs'),
);

import type { SiteLogDraft } from '../siteLogDrafts';

/**
 * Whose evidence a logout is allowed to destroy.
 *
 * The defect these exist for: the explicit logout cleared the whole drafts
 * array and deleted the whole `site-log/` tree. On a shared site phone,
 * worker A's token dies mid-shift - an involuntary logout, which by design
 * KEEPS A's drafts - then worker B signs in, works, and taps Log out. B's
 * logout destroyed A's unsent photos, recordings and documents, which exist
 * nowhere else, with no warning and no way back.
 *
 * Founder ruling: cleanup is scoped to the account signing out.
 */

const STORAGE_KEY = 'site-log-drafts';

function freshModules() {
  jest.resetModules();
  /* eslint-disable @typescript-eslint/no-var-requires */
  const { useSiteLogDrafts } = require('../siteLogDrafts') as typeof import('../siteLogDrafts');
  const session = require('../session') as typeof import('../session');
  const { useFailuresStore } = require('../failures') as typeof import('../failures');
  const { memfs } = require('../../siteLog/__tests__/support/memfs') as typeof import('../../siteLog/__tests__/support/memfs');
  const storageModule = require('@react-native-async-storage/async-storage');
  const AsyncStorage = (storageModule.default ??
    storageModule) as typeof import('@react-native-async-storage/async-storage').default;
  /* eslint-enable @typescript-eslint/no-var-requires */
  return { store: useSiteLogDrafts, session, useFailuresStore, memfs, AsyncStorage };
}

function draftFor(userId: string, capture: string): SiteLogDraft {
  const uri = `file:///documents/site-log/${userId}/${capture}/att-1.jpg`;
  return {
    capture_client_id: capture,
    user_id: userId,
    created_at: 1,
    updated_at: 1,
    declaration: null,
    body_text: `note from ${userId}`,
    job_id: null,
    attachments: [
      {
        attachment_client_id: 'att-1',
        media_type: 'image',
        uri,
        path: `site-log/${userId}/${capture}/att-1.jpg`,
        retained: true,
        name: 'att-1.jpg',
        mime: 'image/jpeg',
        size: 10,
        status: 'awaiting_upload',
      },
    ],
    server: null,
    unconfirmed: false,
    last_message: null,
  };
}

/** Two accounts, each with one unsent capture and its kept file. */
async function twoAccounts() {
  const mod = freshModules();
  // The persist middleware rehydrates the freshly-required store from
  // whatever the previous test left in storage, and it does so a turn
  // later. Let that happen, THEN clear both the storage and the store, so
  // each test starts from exactly two drafts and not two plus leftovers.
  await new Promise((resolve) => setTimeout(resolve, 0));
  mod.memfs.reset();
  await mod.AsyncStorage.clear();
  mod.store.setState({ drafts: [] });

  const a = draftFor('user-a', 'capture-a');
  const b = draftFor('user-b', 'capture-b');
  for (const d of [a, b]) {
    mod.memfs.put(d.attachments[0].uri, 10);
    mod.memfs.dirs.add(`file:///documents/site-log/${d.user_id}/${d.capture_client_id}/`);
    await mod.store.getState().upsertDurable(d);
  }
  return { ...mod, a, b };
}

const fileOf = (d: SiteLogDraft) => d.attachments[0].uri;

describe('clearCaptures', () => {
  it("removes the leaving account's named captures, and nothing else", async () => {
    const { store, memfs, a, b } = await twoAccounts();

    await store.getState().clearCaptures('user-b', ['capture-b']);

    expect(store.getState().forUser('user-b')).toHaveLength(0);
    expect(memfs.files.has(fileOf(b))).toBe(false);

    // The whole point: the other worker is untouched.
    expect(store.getState().forUser('user-a')).toHaveLength(1);
    expect(memfs.files.has(fileOf(a))).toBe(true);
  });

  it("deletes only that account's subtree, not the shared root", async () => {
    const { store, memfs } = await twoAccounts();

    await store.getState().clearCaptures('user-b', ['capture-b']);

    const remaining = [...memfs.files.keys()];
    expect(remaining.some((p) => p.includes('/site-log/user-a/'))).toBe(true);
    expect(remaining.some((p) => p.includes('/site-log/user-b/'))).toBe(false);
  });

  it('has written the change before it returns, so a restart agrees', async () => {
    const { store, AsyncStorage } = await twoAccounts();

    await store.getState().clearCaptures('user-b', ['capture-b']);

    // Read the persisted copy, not the in-memory store.
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    const persisted = JSON.parse(raw ?? '{}') as {
      state: { drafts: SiteLogDraft[] };
    };
    const owners = persisted.state.drafts.map((d) => d.user_id);
    expect(owners).toEqual(['user-a']);
  });

  it('is harmless for an account with nothing on this phone', async () => {
    const { store, memfs, a, b } = await twoAccounts();

    await store.getState().clearCaptures('user-c', ['capture-a', 'capture-b']);

    expect(memfs.files.has(fileOf(a))).toBe(true);
    expect(memfs.files.has(fileOf(b))).toBe(true);
    expect(store.getState().drafts).toHaveLength(2);
  });

  it("will not delete another account's capture even when handed its id", async () => {
    const { store, memfs, a } = await twoAccounts();

    // user-b asking for user-a's capture. The id is real; the owner is not.
    await store.getState().clearCaptures('user-b', ['capture-a']);

    expect(store.getState().forUser('user-a')).toHaveLength(1);
    expect(memfs.files.has(fileOf(a))).toBe(true);
  });
});

describe('wipeOnExplicitLogout', () => {
  it('scopes the drafts to the account leaving, and still clears failures', async () => {
    const { session, useFailuresStore, store, memfs, a, b } = await twoAccounts();
    useFailuresStore.getState().recordFailure({
      inputText: 'half-typed expense',
      errorMessage: 'network',
      context: 'app',
    });
    expect(useFailuresStore.getState().failures.length).toBeGreaterThan(0);

    await session.wipeOnExplicitLogout('user-b', ['capture-b']);

    // Failed-capture texts are device-level and still wiped on an explicit
    // logout - that behaviour is unchanged.
    expect(useFailuresStore.getState().failures).toHaveLength(0);
    // Drafts and files are not.
    expect(store.getState().forUser('user-a')).toHaveLength(1);
    expect(memfs.files.has(fileOf(a))).toBe(true);
    expect(store.getState().forUser('user-b')).toHaveLength(0);
    expect(memfs.files.has(fileOf(b))).toBe(false);
  });

  it("deletes nobody's captures when there is no identifiable account", async () => {
    const { session, store, memfs, a, b } = await twoAccounts();

    await session.wipeOnExplicitLogout(null, ['capture-a', 'capture-b']);

    // Refusing is the safe default: an unknown account is not a licence to
    // delete everyone's evidence.
    expect(store.getState().drafts).toHaveLength(2);
    expect(memfs.files.has(fileOf(a))).toBe(true);
    expect(memfs.files.has(fileOf(b))).toBe(true);
  });

  it("REGRESSION: signing out of one account leaves the other's evidence intact", async () => {
    // The exact sequence from the review finding, end to end.
    const { session, store, memfs, a, b } = await twoAccounts();

    // Worker A was logged out involuntarily: drafts deliberately kept.
    expect(store.getState().forUser('user-a')).toHaveLength(1);

    // Worker B signs in, works, and taps Log out.
    await session.wipeOnExplicitLogout('user-b', ['capture-b']);

    expect(store.getState().forUser('user-a')).toHaveLength(1);
    expect(memfs.files.has(fileOf(a))).toBe(true);
    expect(store.getState().forUser('user-b')).toHaveLength(0);
    expect(memfs.files.has(fileOf(b))).toBe(false);
  });

  it('REGRESSION: a capture saved DURING logout is not deleted by it', async () => {
    // The race the closing review found. Logout awaits /auth/logout before
    // cleaning up; the user can reach the capture screen during that wait.
    // The confirmation named one capture, so only that one may go.
    const { session, store, memfs, b } = await twoAccounts();
    const confirmed = ['capture-b'];

    // ... the network call is in flight, and the user saves another one.
    const late = draftFor('user-b', 'capture-b-late');
    memfs.put(fileOf(late), 10);
    await store.getState().upsertDurable(late);

    // ... then the logout completes and cleans up.
    await session.wipeOnExplicitLogout('user-b', confirmed);

    // The capture they agreed to lose is gone.
    expect(memfs.files.has(fileOf(b))).toBe(false);
    // The one saved afterwards, which no dialog ever mentioned, is not.
    expect(store.getState().get('capture-b-late')).toBeDefined();
    expect(memfs.files.has(fileOf(late))).toBe(true);
  });

  it('an ordinary logout with nothing unsent deletes nothing at all', async () => {
    const { session, store, memfs, a, b } = await twoAccounts();

    // The empty list is what the screen passes when it saw no drafts.
    await session.wipeOnExplicitLogout('user-b', []);

    expect(store.getState().drafts).toHaveLength(2);
    expect(memfs.files.has(fileOf(a))).toBe(true);
    expect(memfs.files.has(fileOf(b))).toBe(true);
  });
});
