/**
 * The phone's list of potential-variation flags whose answer never came
 * (DEC-PV-001, reliability "P"): per account and per entry; the disk, not
 * memory, is what every change is applied to; a failed load says so and
 * can never erase another account's records; it survives a restart; and the
 * explicit logout drops exactly the leaving account's records.
 */
import type { PendingPvCreate } from '../pvPendingCreates';

jest.mock('expo-file-system/legacy', () => require('../../siteLog/__tests__/support/memfs'));

const STORAGE_KEY = 'pv-pending-creates';
type Storage = typeof import('@react-native-async-storage/async-storage').default;

function load() {
  /* eslint-disable @typescript-eslint/no-var-requires */
  const { usePvPendingCreates } = require('../pvPendingCreates') as typeof import('../pvPendingCreates');
  const session = require('../session') as typeof import('../session');
  const storageModule = require('@react-native-async-storage/async-storage');
  /* eslint-enable @typescript-eslint/no-var-requires */
  const AsyncStorage = (storageModule.default ?? storageModule) as Storage;
  return { store: usePvPendingCreates, session, AsyncStorage };
}

function record(overrides: Partial<PendingPvCreate> = {}): PendingPvCreate {
  return {
    create_client_id: 'key-1',
    user_id: 'user-a',
    site_log_event_id: 'event-1',
    source_revision_no: 1,
    job_id: null,
    title: 'Two extra power points in the kitchen',
    created_at: 1,
    ...overrides,
  };
}

async function persisted(AsyncStorage: Storage): Promise<PendingPvCreate[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw).state.pending as PendingPvCreate[]) : [];
}

beforeEach(() => {
  jest.resetModules();
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
});

it('hands out only the asking account’s records, per entry, newest first', async () => {
  const { store } = load();
  await store.getState().putDurable(record());
  await store.getState().putDurable(record({ create_client_id: 'key-b', user_id: 'user-b' }));
  await store.getState().putDurable(record({ create_client_id: 'key-new', created_at: 5 }));
  expect(store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-new');
  expect(store.getState().forEvent('user-b', 'event-1')?.create_client_id).toBe('key-b');
  expect(store.getState().forEvent('user-a', 'event-2')).toBeUndefined();
  expect(store.getState().forUser('user-b').map((p) => p.create_client_id)).toEqual(['key-b']);
});

it('sees a flag being sent before its record reaches the disk, and only its owner does', () => {
  const { store } = load();
  store.getState().markSending(record());
  expect(store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
  expect(store.getState().forEvent('user-b', 'event-1')).toBeUndefined();
  store.getState().unmarkSending('key-1');
  expect(store.getState().forEvent('user-a', 'event-1')).toBeUndefined();
});

it('keeps its own storage, apart from the task store', async () => {
  const { store, AsyncStorage } = load();
  await store.getState().putDurable(record());
  expect(await persisted(AsyncStorage)).toEqual([record()]);
  expect(await AsyncStorage.getItem('task-pending-creates')).toBeNull();
});

it('reports a failed load, and that load cannot erase another account’s records on logout', async () => {
  const first = load();
  await first.store.getState().putDurable(record());

  jest.resetModules(); // restart, and this time the first read fails
  const storageModule = require('@react-native-async-storage/async-storage'); // eslint-disable-line @typescript-eslint/no-var-requires
  const AsyncStorage = (storageModule.default ?? storageModule) as Storage;
  const realGet = AsyncStorage.getItem.bind(AsyncStorage);
  let failed = false;
  jest.spyOn(AsyncStorage, 'getItem').mockImplementation(async (k: string) => {
    if (k === STORAGE_KEY && !failed) {
      failed = true;
      throw new Error('read failed');
    }
    return realGet(k);
  });
  const second = load(); // the store loads itself as it comes up - and fails
  await new Promise((r) => setTimeout(r, 0));
  expect(second.store.getState().hydrated).toBe(false);
  expect(second.store.getState().loadFailed).toBe(true);
  expect(second.store.getState().pending).toEqual([]); // memory is empty...

  await second.session.wipeOnExplicitLogout('user-b', []); // ...B signs out
  expect(await persisted(AsyncStorage)).toEqual([record()]); // A's record is still there

  // Readable again: a retry loads it.
  await second.store.getState().hydrate();
  expect(second.store.getState().hydrated).toBe(true);
  expect(second.store.getState().loadFailed).toBe(false);
  expect(second.store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
});

it('a write that fails rejects, so the caller can refuse to send', async () => {
  const { store, AsyncStorage } = load();
  jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('disk full'));
  await expect(store.getState().putDurable(record())).rejects.toThrow('disk full');
  expect(await persisted(AsyncStorage)).toEqual([]);
});

it('records survive an app restart', async () => {
  const first = load();
  await first.store.getState().putDurable(record({ source_revision_no: 3 }));
  jest.resetModules();
  const second = load();
  await second.store.getState().hydrate();
  expect(second.store.getState().forEvent('user-a', 'event-1')).toEqual(record({ source_revision_no: 3 }));
});

it('the explicit logout drops the leaving account’s flags and no one else’s', async () => {
  const { store, session } = load();
  await store.getState().putDurable(record());
  await store.getState().putDurable(record({ create_client_id: 'key-b', user_id: 'user-b' }));
  await session.wipeOnExplicitLogout('user-a', []);
  expect(store.getState().forUser('user-a')).toEqual([]);
  expect(store.getState().forUser('user-b').map((p) => p.create_client_id)).toEqual(['key-b']);
  // No identifiable account: nothing is touched.
  await session.wipeOnExplicitLogout(null, []);
  expect(store.getState().forUser('user-b')).toHaveLength(1);
});
