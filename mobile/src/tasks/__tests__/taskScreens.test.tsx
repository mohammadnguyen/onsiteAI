/**
 * The task screens, rendered for real with react-test-renderer: what a user
 * may touch comes from the server's `permissions`; the source entry link is
 * replaced by a plain statement when the user may not read the entry; Save
 * sends one request however often it is tapped; and an unresolved create is
 * resolved by asking the server before anything is sent again.
 *
 * Lives under src/ because a file under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';

import type { TaskOut } from '../../api/tasks';

const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockParams: Record<string, string> = {};
// Focus, controlled by the test: blurEverything() is "the user went to
// another screen" while this one stays mounted underneath.
const mockFocusCleanups: (() => void)[] = [];
function blurEverything(): void {
  while (mockFocusCleanups.length > 0) mockFocusCleanups.pop()?.();
}
jest.mock('expo-router', () => ({
  router: {
    push: (...a: unknown[]) => mockPush(...a),
    replace: (...a: unknown[]) => mockReplace(...a),
    back: jest.fn(),
    canGoBack: () => true,
  },
  useLocalSearchParams: () => mockParams,
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const react = require('react') as typeof import('react');
    react.useEffect(() => {
      const cleanup = cb();
      if (cleanup) mockFocusCleanups.push(cleanup);
      return () => {
        if (!cleanup) return;
        const at = mockFocusCleanups.indexOf(cleanup);
        if (at >= 0) {
          mockFocusCleanups.splice(at, 1);
          cleanup();
        }
      };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
  },
}));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  return { ...actual, SafeAreaView: ({ children }: { children: unknown }) => children };
});
jest.mock('@react-native-community/datetimepicker', () => ({ __esModule: true, default: () => null }));

let mockQueryData: unknown;
const mockQueryClient = { setQueryData: jest.fn(), invalidateQueries: jest.fn() };
jest.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ isLoading: false, data: mockQueryData, error: null }),
  useQueryClient: () => mockQueryClient,
}));

const mockMe = { user_id: 'me-1', full_name: 'Me Myself', role: 'contributor' };
jest.mock('../../api/hooks/useAuth', () => ({ useMe: () => ({ data: mockMe }) }));
jest.mock('../../api/hooks/useJobs', () => ({ useJobs: () => ({ data: [] }) }));
jest.mock('../../api/hooks/useUsers', () => ({ useUsers: () => ({ data: [] }) }));
jest.mock('../../siteLog/BackLink', () => ({ BackLink: () => null }));
jest.mock('../../components/JobPickerSheet', () => ({ JobPickerSheet: () => null }));

const mockPatchTask = jest.fn();
const mockCreateTask = jest.fn();
const mockFindMyCreate = jest.fn();
jest.mock('../../api/tasks', () => ({
  TASK_STATUSES: ['to_do', 'in_progress', 'blocked', 'done'],
  patchTask: (...a: unknown[]) => mockPatchTask(...a),
  createTask: (...a: unknown[]) => mockCreateTask(...a),
  findMyCreate: (...a: unknown[]) => mockFindMyCreate(...a),
}));

import { useAuthStore } from '../../store/auth';
import { useTaskPendingCreates } from '../../store/taskPendingCreates';
import TaskDetail from '../../../app/tasks/[id]';
import NewTaskScreen from '../../../app/tasks/new';

function task(overrides: Partial<TaskOut> = {}): TaskOut {
  return {
    task_id: 'task-1',
    title: 'Add roof bracing',
    status: 'to_do',
    origin: 'manual',
    due_date: null,
    job: null,
    assignee: null,
    created_by: { user_id: 'boss', full_name: 'Boss' },
    source: { site_log_event_id: 'event-1', revision_no: 1, readable: true, job_id: null },
    permissions: {
      change_status: true,
      edit: true,
      assign_others: false,
      set_job: false,
      change_job: false,
    },
    created_at: '2026-10-13T00:00:00Z',
    updated_at: '2026-10-13T00:00:00Z',
    ...overrides,
  };
}

const byTestId = (tree: ReactTestRenderer, id: string): ReactTestInstance[] =>
  tree.root.findAll((n) => n.props.testID === id && typeof n.type !== 'string');

// Several turns: the phone-storage mock defers each write by one turn, and a
// create both writes its record and removes it.
async function flush() {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const mounted: ReactTestRenderer[] = [];
async function render(el: React.ReactElement): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(el);
  });
  mounted.push(tree as ReactTestRenderer);
  await flush();
  return tree as ReactTestRenderer;
}

// A screen left mounted would keep reacting to the store in the next test.
afterEach(() => {
  while (mounted.length > 0) {
    const tree = mounted.pop();
    act(() => tree?.unmount());
  }
});

function press(tree: ReactTestRenderer, id: string) {
  const [node] = byTestId(tree, id);
  if (!node) throw new Error(`no element ${id}`);
  act(() => {
    node.props.onPress();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  blurEverything();
  mockParams = {};
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
  useTaskPendingCreates.setState({ pending: [], hydrated: true, loadFailed: false });
  useAuthStore.setState({ userId: 'me-1', sessionNonce: 1, accessToken: 'token-me' });
});

describe('task detail', () => {
  beforeEach(() => {
    mockParams = { id: 'task-1' };
  });

  it('opens a readable source entry', async () => {
    mockQueryData = task();
    const tree = await render(<TaskDetail />);
    expect(byTestId(tree, 'task-source-hidden')).toHaveLength(0);
    press(tree, 'task-open-source');
    expect(mockPush).toHaveBeenCalledWith('/site-log/event-1');
  });

  it('says the source is not visible instead of offering a link that would fail', async () => {
    mockQueryData = task({
      source: { site_log_event_id: 'event-1', revision_no: 1, readable: false, job_id: null },
    });
    const tree = await render(<TaskDetail />);
    expect(byTestId(tree, 'task-open-source')).toHaveLength(0);
    expect(byTestId(tree, 'task-source-hidden')).toHaveLength(1);
  });

  it('one tap moves the status when the server allows it', async () => {
    mockQueryData = task();
    mockPatchTask.mockResolvedValue(task({ status: 'done' }));
    const tree = await render(<TaskDetail />);
    press(tree, 'task-status:done');
    await flush();
    expect(mockPatchTask).toHaveBeenCalledWith('task-1', { status: 'done' });
  });

  it('shows the status but changes nothing when the server does not allow it', async () => {
    mockQueryData = task({
      due_date: '2026-10-17',
      permissions: {
        change_status: false,
        edit: false,
        assign_others: false,
        set_job: false,
        change_job: false,
      },
    });
    const tree = await render(<TaskDetail />);
    press(tree, 'task-status:done');
    await flush();
    expect(mockPatchTask).not.toHaveBeenCalled();
    expect(byTestId(tree, 'task-title-edit')).toHaveLength(0);
    // No greyed-out edit controls for someone who may not edit.
    expect(byTestId(tree, 'due-date-clear')).toHaveLength(0);
    expect(byTestId(tree, 'task-due-readonly')).toHaveLength(1);
  });

  it('a title being edited survives saving a different field', async () => {
    mockQueryData = task();
    mockPatchTask.mockResolvedValue(task({ status: 'blocked' }));
    const tree = await render(<TaskDetail />);
    press(tree, 'task-title-edit');
    const [input] = byTestId(tree, 'task-title-input');
    act(() => input.props.onChangeText('Brace the north roof today'));
    press(tree, 'task-status:blocked');
    await flush();
    expect(mockPatchTask).toHaveBeenCalledWith('task-1', { status: 'blocked' });
    const [still] = byTestId(tree, 'task-title-input');
    expect(still?.props.value).toBe('Brace the north roof today');
  });

  it('a change that finishes after the user left still updates the cached task and lists', async () => {
    mockQueryData = task();
    let finish: (t: TaskOut) => void = () => undefined;
    mockPatchTask.mockImplementation(
      () => new Promise<TaskOut>((resolve) => {
        finish = resolve;
      }),
    );
    const tree = await render(<TaskDetail />);
    press(tree, 'task-status:done');
    await flush();
    blurEverything(); // the user went elsewhere; this screen stays mounted
    await act(async () => {
      finish(task({ status: 'done' }));
    });
    await flush();
    expect(mockQueryClient.setQueryData).toHaveBeenCalled();
    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['tasks', 'list'] });
    expect(tree).toBeTruthy();
  });

  it('notes when the source entry has moved to another job, and the task stays', async () => {
    mockQueryData = task({
      job: { job_id: 'job-a', job_name: 'A', job_code: null, status: 'active' },
      source: { site_log_event_id: 'event-1', revision_no: 1, readable: true, job_id: 'job-b' },
    });
    const tree = await render(<TaskDetail />);
    expect(byTestId(tree, 'task-entry-moved')).toHaveLength(1);
  });
});

describe('creating a task', () => {
  const ENTRY = {
    site_log_event_id: 'event-1',
    job_id: null,
    revision: { body_text: 'Engineer asks for extra roof bracing\nNorth side', withdrawn: false },
  };

  beforeEach(() => {
    mockParams = { eventId: 'event-1' };
    mockQueryData = ENTRY;
    mockFindMyCreate.mockResolvedValue(null);
  });

  it('pre-fills the title from the entry and sends ONE request however often Save is tapped', async () => {
    let finish: (t: TaskOut) => void = () => undefined;
    mockCreateTask.mockImplementation(
      () => new Promise<TaskOut>((resolve) => {
        finish = resolve;
      }),
    );
    const tree = await render(<NewTaskScreen />);
    const [input] = byTestId(tree, 'task-title');
    expect(input.props.value).toBe('Engineer asks for extra roof bracing');

    press(tree, 'task-save');
    await flush();
    press(tree, 'task-save');
    await flush();
    expect(mockCreateTask).toHaveBeenCalledTimes(1);
    expect(byTestId(tree, 'task-save')[0].props.disabled).toBe(true);
    // The record is on the phone while the request is out.
    expect(useTaskPendingCreates.getState().forEvent('me-1', 'event-1')).toBeDefined();

    await act(async () => {
      finish(task());
    });
    await flush();
    expect(mockReplace).toHaveBeenCalledWith('/tasks/task-1');
    expect(useTaskPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
  });

  it('an unresolved create is checked with the server first, then resent under the SAME key', async () => {
    await act(async () => {
      await useTaskPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        title: 'Brace the north roof',
        assignee_user_id: 'me-1',
        assignee_name: 'Me Myself',
        due_date: null,
        created_at: 1,
      });
    });
    mockFindMyCreate.mockResolvedValue(null);
    mockCreateTask.mockResolvedValue(task());
    const tree = await render(<NewTaskScreen />);
    await flush();
    expect(mockFindMyCreate).toHaveBeenCalledWith('lost-key');
    expect(byTestId(tree, 'pending-not-found')).toHaveLength(1);
    expect(byTestId(tree, 'task-save')).toHaveLength(0); // no second create offered

    press(tree, 'pending-send-again');
    await flush();
    expect(mockCreateTask).toHaveBeenCalledWith('event-1', {
      create_client_id: 'lost-key',
      title: 'Brace the north roof',
      assignee_user_id: 'me-1',
      due_date: null,
    });
    expect(mockReplace).toHaveBeenCalledWith('/tasks/task-1');
  });

  it('a create that succeeds after the user left refreshes the lists but moves nobody', async () => {
    let finish: (t: TaskOut) => void = () => undefined;
    mockCreateTask.mockImplementation(
      () => new Promise<TaskOut>((resolve) => {
        finish = resolve;
      }),
    );
    const tree = await render(<NewTaskScreen />);
    press(tree, 'task-save');
    await flush();
    expect(mockCreateTask).toHaveBeenCalledTimes(1);
    blurEverything(); // back on the entry screen, which is still mounted
    await act(async () => {
      finish(task());
    });
    await flush();
    // The entry screen's list must learn of the task, or it offers a second create.
    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['tasks'] });
    expect(mockReplace).not.toHaveBeenCalled();
    expect(useTaskPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
  });

  it('nothing is sent once the account has changed', async () => {
    mockCreateTask.mockResolvedValue(task());
    const tree = await render(<NewTaskScreen />);
    act(() => {
      useAuthStore.setState({ userId: 'someone-else', sessionNonce: 2 });
    });
    press(tree, 'task-save');
    await flush();
    expect(mockCreateTask).not.toHaveBeenCalled();
  });

  it('when the phone’s list cannot be read, no new create is offered until it can', async () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const storage = require('@react-native-async-storage/async-storage').default;
    const unreadable = jest.spyOn(storage, 'getItem').mockRejectedValue(new Error('read failed'));
    try {
      useTaskPendingCreates.setState({ pending: [], hydrated: false, loadFailed: false });
      const tree = await render(<NewTaskScreen />); // the screen's own load attempt fails
      await flush();
      expect(byTestId(tree, 'task-save')).toHaveLength(0);
      expect(byTestId(tree, 'task-store-retry').length).toBeGreaterThan(0);

      unreadable.mockRestore(); // readable again: retry brings the form back
      press(tree, 'task-store-retry');
      await flush();
      expect(byTestId(tree, 'task-save').length).toBeGreaterThan(0);
    } finally {
      unreadable.mockRestore();
    }
  });

  it('an unresolved create the server already has opens that task (even though resolving unmounts the panel)', async () => {
    await act(async () => {
      await useTaskPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        title: 'x',
        assignee_user_id: null,
        assignee_name: null,
        due_date: null,
        created_at: 1,
      });
    });
    mockFindMyCreate.mockResolvedValue(task({ task_id: 'task-9' }));
    await render(<NewTaskScreen />);
    await flush();
    expect(mockCreateTask).not.toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/tasks/task-9?notice=already_created');
    expect(useTaskPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
  });

  it('a withdrawn entry offers no create', async () => {
    mockQueryData = { ...ENTRY, revision: { ...ENTRY.revision, withdrawn: true } };
    const tree = await render(<NewTaskScreen />);
    expect(byTestId(tree, 'task-save')).toHaveLength(0);
  });
});
