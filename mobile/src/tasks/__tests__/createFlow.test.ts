/**
 * Duplicate-safe task creation (plan v2 §5), without a screen.
 *
 * What these pin: the pending record is on disk before the request leaves;
 * a definite answer removes it, an unknown one keeps it; a lost answer is
 * resolved by ASKING the server, and a resend reuses the same key and the
 * same values; the explicit logout drops only the signing-out account's
 * records; and records survive an app restart.
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

function load() {
  /* eslint-disable @typescript-eslint/no-var-requires */
  const flow = require('../createFlow') as typeof import('../createFlow');
  const { useTaskPendingCreates } = require('../../store/taskPendingCreates') as typeof import('../../store/taskPendingCreates');
  const session = require('../../store/session') as typeof import('../../store/session');
  const storageModule = require('@react-native-async-storage/async-storage');
  /* eslint-enable @typescript-eslint/no-var-requires */
  const AsyncStorage = (storageModule.default ??
    storageModule) as typeof import('@react-native-async-storage/async-storage').default;
  return { flow, store: useTaskPendingCreates, session, AsyncStorage };
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

async function persisted(AsyncStorage: { getItem: (k: string) => Promise<string | null> }) {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw).state.pending as PendingTaskCreate[]) : [];
}

beforeEach(async () => {
  jest.resetModules();
  mockCreateTask.mockReset();
  mockFindMyCreate.mockReset();
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
});

describe('sending a create', () => {
  it('writes the pending record to disk before the request is sent', async () => {
    const { flow, AsyncStorage } = load();
    let onDiskWhenSent: PendingTaskCreate[] = [];
    mockCreateTask.mockImplementation(async () => {
      onDiskWhenSent = await persisted(AsyncStorage);
      return TASK;
    });
    const outcome = await flow.sendCreate(record());
    expect(onDiskWhenSent.map((p) => p.create_client_id)).toEqual(['key-1']);
    expect(outcome).toEqual({ kind: 'created', task: TASK });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it('sends exactly the recorded key and values', async () => {
    const { flow } = load();
    mockCreateTask.mockResolvedValue(TASK);
    await flow.sendCreate(record({ assignee_user_id: 'user-b', assignee_name: 'B' }));
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
    const { flow, AsyncStorage } = load();
    mockCreateTask.mockRejectedValue(err);
    const outcome = await flow.sendCreate(record());
    expect(outcome).toMatchObject({ kind: 'refused', status });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it.each([
    ['no connection', networkError()],
    ['a timeout', Object.assign(new Error('timeout'), { isAxiosError: true, code: 'ECONNABORTED' })],
    ['a server error (503)', httpError(503)],
    ['an ended session (401)', httpError(401)],
  ])('keeps the record when the result is unknown: %s', async (_label, err) => {
    const { flow, AsyncStorage } = load();
    mockCreateTask.mockRejectedValue(err);
    const outcome = await flow.sendCreate(record());
    expect(outcome).toEqual({ kind: 'unknown' });
    expect((await persisted(AsyncStorage)).map((p) => p.create_client_id)).toEqual(['key-1']);
  });
});

describe('finding out what happened to a lost create', () => {
  it('found: the record is done with and the task is returned', async () => {
    const { flow, store, AsyncStorage } = load();
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record());
    mockFindMyCreate.mockResolvedValue(TASK);
    const outcome = await flow.resolvePending(store.getState().forUser('user-a')[0]);
    expect(mockFindMyCreate).toHaveBeenCalledWith('key-1');
    expect(outcome).toEqual({ kind: 'found', task: TASK });
    expect(await persisted(AsyncStorage)).toEqual([]);
  });

  it('not found: the record stays, and a resend reuses the same key and values', async () => {
    const { flow, store } = load();
    mockCreateTask.mockRejectedValueOnce(networkError());
    await flow.sendCreate(record());
    mockFindMyCreate.mockResolvedValue(null);
    const pending = store.getState().forEvent('user-a', 'event-1');
    expect(pending).toBeDefined();
    expect(await flow.resolvePending(pending!)).toEqual({ kind: 'not_found' });
    expect(store.getState().forEvent('user-a', 'event-1')).toBeDefined();

    mockCreateTask.mockResolvedValueOnce(TASK);
    await flow.sendCreate(store.getState().forEvent('user-a', 'event-1')!);
    const [first, second] = mockCreateTask.mock.calls;
    expect(second).toEqual(first); // same event, same key, same body
    expect(store.getState().forEvent('user-a', 'event-1')).toBeUndefined();
  });

  it('still unreachable: unknown, and nothing changes', async () => {
    const { flow, store, AsyncStorage } = load();
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record());
    mockFindMyCreate.mockRejectedValue(networkError());
    const outcome = await flow.resolvePending(store.getState().forUser('user-a')[0]);
    expect(outcome).toEqual({ kind: 'unknown' });
    expect((await persisted(AsyncStorage)).length).toBe(1);
  });

  it('discard removes only that record', async () => {
    const { flow, store } = load();
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record());
    await flow.sendCreate(record({ create_client_id: 'key-2', site_log_event_id: 'event-2' }));
    await flow.discardPending('key-1');
    expect(store.getState().forUser('user-a').map((p) => p.create_client_id)).toEqual(['key-2']);
  });
});

describe('whose records they are', () => {
  it('hands out only the current account’s records, per entry', async () => {
    const { flow, store } = load();
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record());
    await flow.sendCreate(record({ create_client_id: 'key-b', user_id: 'user-b' }));
    expect(store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
    expect(store.getState().forEvent('user-b', 'event-1')?.create_client_id).toBe('key-b');
    expect(store.getState().forEvent('user-a', 'event-2')).toBeUndefined();
  });

  it('the explicit logout drops the signing-out account’s records and no one else’s', async () => {
    const { flow, store, session } = load();
    mockCreateTask.mockRejectedValue(networkError());
    await flow.sendCreate(record());
    await flow.sendCreate(record({ create_client_id: 'key-b', user_id: 'user-b' }));
    await session.wipeOnExplicitLogout('user-a', []);
    expect(store.getState().forUser('user-a')).toEqual([]);
    expect(store.getState().forUser('user-b').map((p) => p.create_client_id)).toEqual(['key-b']);
    // No identifiable account: nothing is touched.
    await session.wipeOnExplicitLogout(null, []);
    expect(store.getState().forUser('user-b')).toHaveLength(1);
  });

  it('records survive an app restart', async () => {
    const first = load();
    mockCreateTask.mockRejectedValue(networkError());
    await first.flow.sendCreate(record());

    jest.resetModules(); // the app restarts; storage is the same
    const second = load();
    await second.store.persist.rehydrate();
    expect(second.store.getState().forEvent('user-a', 'event-1')?.create_client_id).toBe('key-1');
  });
});
