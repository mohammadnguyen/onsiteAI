/**
 * Duplicate-safe task creation (plan v2 §5), without a screen.
 *
 * What these pin: the pending record is on disk before the request leaves;
 * a definite answer removes it, an unknown one keeps it; a lost answer is
 * resolved by ASKING the server, and a resend reuses the same key and the
 * same values; nothing is sent for an account other than the one that
 * acted; the phone's storage, not a copy in memory, is what every change is
 * applied to, so a failed load can never erase another account's records;
 * and records survive an app restart.
 */
import type { TaskOut } from '../../api/tasks';
import type { PendingTaskCreate } from '../../store/taskPendingCreates';

const mockCreateTask = jest.fn();
const mockFindMyCreate = jest.fn();
jest.mock('../../api/tasks', () => ({
  createTask: (...a: unknown[]) => mockCreateTask(...a),
  findMyCreate: (...a: unknown[]) => mockFindMyCreate(...a),
}));
jest.mock('expo-file-system/legacy', () => require('../../siteLog/__tests__/support/memfs'));

const STORAGE_KEY = 'task-pending-creates';
type Storage = typeof import('@react-native-async-storage/async-storage').default;

function load() {
  /* eslint-disable @typescript-eslint/no-var-requires */
  const flow = require('../createFlow') as typeof import('../createFlow');
  const { useTaskPendingCreates } = require('../../store/taskPendingCreates') as typeof import('../../store/taskPendingCreates');
  const { useAuthStore } = require('../../store/auth') as typeof import('../../store/auth');
  const session = require('../../store/session') as typeof import('../../store/session');
  const storageModule = require('@react-native-async-storage/async-storage');
  /* eslint-enable @typescript-eslint/no-var-requires */
  const AsyncStorage = (storageModule.default ?? storageModule) as Storage;
  return { flow, store: useTaskPendingCreates, auth: useAuthStore, session, AsyncStorage };
}

/** Signed in as `userId`, in session `nonce`. */
function signIn(auth: ReturnType<typeof load>['auth'], userId: string, nonce: number) {
  auth.setState({ userId, sessionNonce: nonce, accessToken: `token-${userId}` });
}

function record(overrides: Partial<PendingTaskCreate> = {}): PendingTaskCreate {
  return {
    create_client_id: 'key-1',
    user_id: 'user-a',
    site_log_event_id: 'event-1',
    title: 'Add roof bracing',
    assignee_user_id: null,
    assignee_name: null,
    due_date: '2026-10-17',
    created_at: 1,
    ...overrides,
  };
}

const TASK = { task_id: 'task-1' } as unknown as TaskOut;

function httpError(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data: detail ? { detail } : {} },
  });
}

function networkError() {
  return Object.assign(new Error('Network Error'), { isAxiosError: true, code: 'ERR_NETWORK' });
}

async function persisted(AsyncStorage: Storage): Promise<PendingTaskCreate[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw).state.pending as PendingTaskCreate[]) : [];
}

beforeEach(() => {
  jest.resetModules();
  mockCreateTask.mockReset();
  mockFindMyCreate.mockReset();
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
});

describe('sending a create', () => {
  it('writes the pending record to disk before the request is sent', async () => {
    const { flow, auth, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    let onDiskWhenSent: PendingTaskCreate[] = [];
    mockCreateTask.mockImplementation(async () => {
      onDiskWhenSent = await persisted(AsyncStorage);
      return TASK;
    });
    const outcome = await flow.sendCreate(record(), 1);
    expect(onDiskWhenSent.map((p) => p.create_client_id)).toEqual(['key-1']);
    expect(outcome).toEqual({ kind: 'created', task: TASK });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it('sends exactly the recorded key and values', async () => {
    const { flow, auth } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockResolvedValue(TASK);
    await flow.sendCreate(record({ assignee_user_id: 'user-b', assignee_name: 'B' }), 1);
    expect(mockCreateTask).toHaveBeenCalledWith('event-1', {
      create_client_id: 'key-1',
      title: 'Add roof bracing',
      assignee_user_id: 'user-b',
      due_date: '2026-10-17',
    });
  });

  it.each([
    ['a refusal (422)', httpError(422, 'the assignee must be an active user'), 422],
    ['a withdrawn entry (409)', httpError(409, 'The source entry is withdrawn'), 409],
    ['an unreadable entry (404)', httpError(404, 'Site log event not found'), 404],
  ])('drops the record on %s and reports it', async (_label, err, status) => {
    const { flow, auth, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(err);
    const outcome = await flow.sendCreate(record(), 1);
    expect(outcome).toMatchObject({ kind: 'refused', status });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it.each([
    ['no connection', networkError()],
    ['a timeout', Object.assign(new Error('timeout'), { isAxiosError: true, code: 'ECONNABORTED' })],
    ['a server error (503)', httpError(503)],
    ['an ended session (401)', httpError(401)],
  ])('keeps the record when the result is unknown: %s', async (_label, err) => {
    const { flow, auth, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(err);
    const outcome = await flow.sendCreate(record(), 1);
    expect(outcome).toEqual({ kind: 'unknown' });
    expect((await persisted(AsyncStorage)).map((p) => p.create_client_id)).toEqual(['key-1']);
  });

  it('sends nothing when the record cannot be written to the phone', async () => {
    const { flow, auth, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('disk full'));
    const outcome = await flow.sendCreate(record(), 1);
    expect(outcome).toEqual({ kind: 'not_saved' });
    expect(mockCreateTask).not.toHaveBeenCalled();
  });
});

describe('only the account that acted', () => {
  it('sends nothing if the account changed while the record was being written', async () => {
    const { flow, auth, store, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    // Hold the write, switch accounts inside it, then let it finish.
    const realSet = AsyncStorage.setItem.bind(AsyncStorage);
    let release: () => void = () => undefined;
    jest.spyOn(AsyncStorage, 'setItem').mockImplementationOnce(async (k: string, v: string) => {
      await new Promise<void>((r) => {
        release = r;
      });
      return realSet(k, v);
    });
    const sending = flow.sendCreate(record(), 1);
    await new Promise((r) => setTimeout(r, 0));
    signIn(auth, 'user-b', 2); // A signed out, B signed in
    release();
    expect(await sending).toEqual({ kind: 'stale' });
    expect(mockCreateTask).not.toHaveBeenCalled();
    // A's record is kept for A's next visit; B is never shown it.
    expect(store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
    expect(store.getState().forEvent('user-b', 'event-1')).toBeUndefined();
  });

  it('sends nothing for a record that is not the signed-in account’s', async () => {
    const { flow, auth, AsyncStorage } = load();
    signIn(auth, 'user-b', 2);
    expect(await flow.sendCreate(record(), 2)).toEqual({ kind: 'stale' });
    expect(mockCreateTask).not.toHaveBeenCalled();
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it('a lookup answered after the account changed concludes nothing', async () => {
    const { flow, auth, store } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record(), 1);
    mockFindMyCreate.mockImplementation(async () => {
      signIn(auth, 'user-b', 2);
      return TASK;
    });
    expect(await flow.resolvePending(store.getState().forUser('user-a')[0], 1)).toEqual({
      kind: 'stale',
    });
    expect(store.getState().forEvent('user-a', 'event-1')).toBeDefined();
  });
});

describe('finding out what happened to a lost create', () => {
  it('found: the record is done with and the task is returned', async () => {
    const { flow, auth, store, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record(), 1);
    mockFindMyCreate.mockResolvedValue(TASK);
    const outcome = await flow.resolvePending(store.getState().forUser('user-a')[0], 1);
    expect(mockFindMyCreate).toHaveBeenCalledWith('key-1');
    expect(outcome).toEqual({ kind: 'found', task: TASK });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it('not found: the record stays, and a resend reuses the same key and values', async () => {
    const { flow, auth, store } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValueOnce(networkError());
    await flow.sendCreate(record(), 1);
    mockFindMyCreate.mockResolvedValue(null);
    const pending = store.getState().forEvent('user-a', 'event-1');
    expect(pending).toBeDefined();
    expect(await flow.resolvePending(pending!, 1)).toEqual({ kind: 'not_found' });
    expect(store.getState().forEvent('user-a', 'event-1')).toBeDefined();

    mockCreateTask.mockResolvedValueOnce(TASK);
    await flow.sendCreate(store.getState().forEvent('user-a', 'event-1')!, 1);
    const [first, second] = mockCreateTask.mock.calls;
    expect(second).toEqual(first); // same event, same key, same body
    expect(store.getState().forEvent('user-a', 'event-1')).toBeUndefined();
  });

  it('still unreachable: unknown, and nothing changes', async () => {
    const { flow, auth, store, AsyncStorage } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record(), 1);
    mockFindMyCreate.mockRejectedValue(networkError());
    const outcome = await flow.resolvePending(store.getState().forUser('user-a')[0], 1);
    expect(outcome).toEqual({ kind: 'unknown' });
    expect((await persisted(AsyncStorage)).length).toBe(1);
  });

  it('discard removes only that record', async () => {
    const { flow, auth, store } = load();
    signIn(auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record(), 1);
    await flow.sendCreate(record({ create_client_id: 'key-2', site_log_event_id: 'event-2' }), 1);
    await flow.discardPending('key-1');
    expect(store.getState().forUser('user-a').map((p) => p.create_client_id)).toEqual(['key-2']);
  });
});

describe('whose records they are, and where they live', () => {
  it('hands out only the current account’s records, per entry', async () => {
    const { flow, auth, store } = load();
    mockCreateTask.mockRejectedValue(networkError());
    signIn(auth, 'user-a', 1);
    await flow.sendCreate(record(), 1);
    signIn(auth, 'user-b', 2);
    await flow.sendCreate(record({ create_client_id: 'key-b', user_id: 'user-b' }), 2);
    expect(store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
    expect(store.getState().forEvent('user-b', 'event-1')?.create_client_id).toBe('key-b');
    expect(store.getState().forEvent('user-a', 'event-2')).toBeUndefined();
  });

  it('the explicit logout drops the signing-out account’s records and no one else’s', async () => {
    const { flow, auth, store, session } = load();
    mockCreateTask.mockRejectedValue(networkError());
    signIn(auth, 'user-a', 1);
    await flow.sendCreate(record(), 1);
    signIn(auth, 'user-b', 2);
    await flow.sendCreate(record({ create_client_id: 'key-b', user_id: 'user-b' }), 2);
    await session.wipeOnExplicitLogout('user-a', []);
    expect(store.getState().forUser('user-a')).toEqual([]);
    expect(store.getState().forUser('user-b').map((p) => p.create_client_id)).toEqual(['key-b']);
    // No identifiable account: nothing is touched.
    await session.wipeOnExplicitLogout(null, []);
    expect(store.getState().forUser('user-b')).toHaveLength(1);
  });

  it('a store that failed to load cannot erase another account’s records on logout', async () => {
    // A's unresolved create is on the phone from an earlier run.
    const first = load();
    signIn(first.auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await first.flow.sendCreate(record(), 1);

    jest.resetModules(); // restart, and this time the first read fails
    const storageModule = require('@react-native-async-storage/async-storage'); // eslint-disable-line @typescript-eslint/no-var-requires
    const AsyncStorage = (storageModule.default ?? storageModule) as Storage;
    // Only this store's first read fails (other stores read storage too).
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
    // A's record is still on the phone.
    expect((await persisted(AsyncStorage)).map((p) => p.create_client_id)).toEqual(['key-1']);
  });

  it('records survive an app restart', async () => {
    const first = load();
    signIn(first.auth, 'user-a', 1);
    mockCreateTask.mockRejectedValue(networkError());
    await first.flow.sendCreate(record(), 1);

    jest.resetModules(); // the app restarts; storage is the same
    const second = load();
    await second.store.getState().hydrate();
    expect(second.store.getState().hydrated).toBe(true);
    expect(second.store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
  });
});
