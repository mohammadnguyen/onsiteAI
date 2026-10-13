/**
 * The task screens, rendered for real with react-test-renderer: what a user
 * may touch comes from the server's `permissions`; the source entry link is
 * replaced by a plain statement when the user may not read the entry; Save
 * sends one request however often it is tapped; an unresolved create is
 * resolved by asking the server before anything is sent again - unless its
 * request is still out, whose own answer is then awaited; and a mistyped due
 * date is never dropped silently.
 *
 * Lives under src/ because a file under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';
import { KeyboardAvoidingView } from 'react-native';

import type { TaskOut } from '../../api/tasks';

const mockPush = jest.fn();
const mockReplace = jest.fn();
// Whether the leave question was armed at the moment each replace happened.
let mockGuardAtReplace: (boolean | null)[] = [];
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
    replace: (...a: unknown[]) => {
      mockGuardAtReplace.push(mockLeave ? mockLeave.prevent : null);
      mockReplace(...a);
    },
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
// The leave guard (PR #22's hook), as its own tests drive it: only the most
// recently mounted screen reports its flag and its handler.
type MockPrevented = (o: { data: { action: unknown } }) => void;
let mockLeave: { prevent: boolean; onPrevented: MockPrevented } | null = null;
let mockNewestScreen: symbol | null = null;
const mockDispatch = jest.fn();
jest.mock('@react-navigation/native', () => ({
  usePreventRemove: (prevent: boolean, onPrevented: MockPrevented) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const screen = require('react').useRef(null) as { current: symbol | null };
    if (screen.current === null) {
      screen.current = Symbol('screen');
      mockNewestScreen = screen.current;
    }
    if (screen.current === mockNewestScreen) mockLeave = { prevent, onPrevented };
  },
  useNavigation: () => ({ dispatch: mockDispatch }),
}));
const mockConfirm = jest.fn();
jest.mock('../../siteLog/dialogs', () => ({
  ...jest.requireActual('../../siteLog/dialogs'),
  confirmDestructive: (args: unknown) => mockConfirm(args),
}));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  return { ...actual, SafeAreaView: ({ children }: { children: unknown }) => children };
});
jest.mock('@react-native-community/datetimepicker', () => ({ __esModule: true, default: () => null }));

let mockQueryData: unknown;
const mockQueryClient = {
  setQueryData: jest.fn(),
  invalidateQueries: jest.fn(),
  cancelQueries: jest.fn(() => Promise.resolve()),
};
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
import { DueDateField, isTypedDueValid } from '../DueDateField';
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
      change_assignee: true,
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

/** The user goes back: the screen is popped, and unmounts. */
function leave(tree: ReactTestRenderer) {
  mounted.splice(mounted.indexOf(tree), 1);
  act(() => tree.unmount());
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
  mockLeave = null;
  mockGuardAtReplace = [];
  mockParams = {};
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
  useTaskPendingCreates.setState({ pending: [], sending: [], hydrated: true, loadFailed: false });
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
        change_assignee: false,
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

  it('a saved change first cancels an older read of the task, which would put the old values back', async () => {
    mockQueryData = task();
    mockPatchTask.mockResolvedValue(task({ status: 'done' }));
    const tree = await render(<TaskDetail />);
    press(tree, 'task-status:done');
    await flush();
    expect(mockQueryClient.cancelQueries).toHaveBeenCalledWith({
      queryKey: ['tasks', 'detail', 'task-1'],
      exact: true,
    });
    const [cancelled] = mockQueryClient.cancelQueries.mock.invocationCallOrder;
    const [installed] = mockQueryClient.setQueryData.mock.invocationCallOrder;
    expect(cancelled).toBeLessThan(installed);
  });

  it('keeps its editing controls above the keyboard', async () => {
    mockQueryData = task();
    const tree = await render(<TaskDetail />);
    const [avoiding] = tree.root.findAllByType(KeyboardAvoidingView);
    expect(avoiding.props.behavior).toBe('padding'); // iOS
    expect(avoiding.findAll((n) => n.props.testID === 'task-title-edit').length).toBeGreaterThan(0);
  });

  it('a task assigned to someone else: its creator sees who, with no control to change it', async () => {
    mockQueryData = task({
      assignee: { user_id: 'other-1', full_name: 'Someone Else' },
      permissions: {
        change_status: true,
        edit: true,
        change_assignee: false,
        assign_others: false,
        set_job: false,
        change_job: false,
      },
    });
    const tree = await render(<TaskDetail />);
    expect(byTestId(tree, 'assignee-me')).toHaveLength(0);
    expect(byTestId(tree, 'assignee-none')).toHaveLength(0);
    const named = tree.root.findAll(
      (n) => typeof n.type === 'string' && n.props.children === 'Someone Else',
    );
    expect(named.length).toBeGreaterThan(0);
    // The rest of the creator's editing is unchanged by the ruling.
    expect(tree.root.findAllByType(DueDateField)).toHaveLength(1);
    expect(byTestId(tree, 'task-title-edit').length).toBeGreaterThan(0);
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

  describe('Save, leave while it is out, open the create again', () => {
    function outstanding() {
      const answer: { give: (t: TaskOut) => void; fail: (e: unknown) => void } = {
        give: () => undefined,
        fail: () => undefined,
      };
      mockCreateTask.mockImplementation(
        () => new Promise<TaskOut>((resolve, reject) => {
          answer.give = resolve;
          answer.fail = reject;
        }),
      );
      return answer;
    }

    async function saveThenReopen() {
      const first = await render(<NewTaskScreen />);
      press(first, 'task-save');
      await flush();
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
      leave(first); // the request is still out
      return render(<NewTaskScreen />);
    }

    it('waits for that answer, asks nothing, offers nothing, then opens the task', async () => {
      const answer = outstanding();
      const reopened = await saveThenReopen();
      // The server would say "not there yet", which reads as "not saved".
      expect(mockFindMyCreate).not.toHaveBeenCalled();
      expect(byTestId(reopened, 'pending-checking').length).toBeGreaterThan(0);
      expect(byTestId(reopened, 'pending-send-again')).toHaveLength(0);
      expect(byTestId(reopened, 'pending-discard')).toHaveLength(0);
      expect(byTestId(reopened, 'task-save')).toHaveLength(0);

      await act(async () => {
        answer.give(task({ task_id: 'task-7' }));
      });
      await flush();
      expect(mockReplace).toHaveBeenCalledWith('/tasks/task-7?notice=already_created');
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
      expect(useTaskPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
    });

    it('when the server refuses it, the form comes back with those values and the reason', async () => {
      const answer = outstanding();
      const reopened = await saveThenReopen();
      await act(async () => {
        answer.fail(
          Object.assign(new Error('HTTP 409'), {
            isAxiosError: true,
            response: { status: 409, data: { detail: 'The source entry is withdrawn' } },
          }),
        );
      });
      await flush();
      expect(mockFindMyCreate).not.toHaveBeenCalled();
      const [title] = byTestId(reopened, 'task-title');
      expect(title.props.value).toBe('Engineer asks for extra roof bracing');
      const shown = reopened.root.findAll(
        (n) => typeof n.type === 'string' && n.props.children === 'The source entry is withdrawn',
      );
      expect(shown.length).toBeGreaterThan(0);
      // Those values now exist only on this screen: leaving asks first.
      expect(mockLeave?.prevent).toBe(true);
    });

    it('even before its record reaches the phone, the reopened screen joins that create', async () => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const storage = require('@react-native-async-storage/async-storage').default;
      const realSet = storage.setItem.bind(storage);
      let releaseWrite: () => void = () => undefined;
      const held = new Promise<void>((r) => {
        releaseWrite = r;
      });
      const slowWrite = jest
        .spyOn(storage, 'setItem')
        .mockImplementation(async (k: unknown, v: unknown) => {
          if (k === 'task-pending-creates') await held;
          return realSet(k, v);
        });
      try {
        const answer = outstanding();
        const first = await render(<NewTaskScreen />);
        press(first, 'task-save');
        await flush();
        expect(mockCreateTask).not.toHaveBeenCalled(); // still writing the record
        leave(first);
        const reopened = await render(<NewTaskScreen />);
        expect(byTestId(reopened, 'task-save')).toHaveLength(0); // no second create offered
        expect(byTestId(reopened, 'pending-checking').length).toBeGreaterThan(0);
        expect(mockFindMyCreate).not.toHaveBeenCalled();

        await act(async () => {
          releaseWrite();
        });
        await flush();
        expect(mockCreateTask).toHaveBeenCalledTimes(1);
        await act(async () => {
          answer.give(task({ task_id: 'task-8' }));
        });
        await flush();
        expect(mockReplace).toHaveBeenCalledWith('/tasks/task-8?notice=already_created');
        expect(mockCreateTask).toHaveBeenCalledTimes(1);
      } finally {
        slowWrite.mockRestore();
      }
    });

    it('when that answer is lost, then asks the server before anything is resent', async () => {
      const answer = outstanding();
      const reopened = await saveThenReopen();
      mockFindMyCreate.mockResolvedValue(null);
      await act(async () => {
        answer.fail(Object.assign(new Error('Network Error'), { isAxiosError: true }));
      });
      await flush();
      expect(mockFindMyCreate).toHaveBeenCalledTimes(1);
      expect(byTestId(reopened, 'pending-not-found')).toHaveLength(1);
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
    });
  });

  it('keeps Save above the keyboard', async () => {
    const tree = await render(<NewTaskScreen />);
    const [avoiding] = tree.root.findAllByType(KeyboardAvoidingView);
    expect(avoiding.props.behavior).toBe('padding'); // iOS
    expect(avoiding.findAll((n) => n.props.testID === 'task-save').length).toBeGreaterThan(0);
  });

  it('a typed due date that is not a date blocks Save until it is one, or gone', async () => {
    mockCreateTask.mockResolvedValue(task());
    const tree = await render(<NewTaskScreen />);
    const field = tree.root.findByType(DueDateField);
    act(() => field.props.onValidityChange(false));
    expect(byTestId(tree, 'task-save')[0].props.disabled).toBe(true);
    press(tree, 'task-save');
    await flush();
    expect(mockCreateTask).not.toHaveBeenCalled();

    act(() => field.props.onValidityChange(true));
    expect(byTestId(tree, 'task-save')[0].props.disabled).toBe(false);
  });

  it.each([
    ['', true],
    ['  ', true],
    ['17/10/2026', true],
    ['31/02/2026', false],
    ['next friday', false],
  ])('typed due date %j can become the value: %s', (text, valid) => {
    expect(isTypedDueValid(text)).toBe(valid);
  });
});

describe('leaving the create screen (PR #22’s guard, in the task’s words)', () => {
  const ENTRY = {
    site_log_event_id: 'event-1',
    job_id: null,
    revision: { body_text: 'Engineer asks for extra roof bracing\nNorth side', withdrawn: false },
  };
  const BACK = { type: 'GO_BACK' };
  type Question = { title: string; confirmLabel: string; cancelLabel: string; onConfirm: () => void };
  const lastQuestion = (): Question =>
    mockConfirm.mock.calls[mockConfirm.mock.calls.length - 1][0] as Question;

  beforeEach(() => {
    mockParams = { eventId: 'event-1' };
    mockQueryData = ENTRY;
    mockFindMyCreate.mockResolvedValue(null);
  });

  function tryToLeave(): void {
    act(() => {
      mockLeave?.onPrevented({ data: { action: BACK } });
    });
  }

  /** The pending-create write to the phone, held until the test lets it go. */
  function holdTheWrite(fail?: Error) {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const storage = require('@react-native-async-storage/async-storage').default;
    const realSet = storage.setItem.bind(storage);
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => {
      release = r;
    });
    const spy = jest
      .spyOn(storage, 'setItem')
      .mockImplementation(async (k: unknown, v: unknown) => {
        if (k === 'task-pending-creates') {
          await held;
          if (fail) throw fail;
        }
        return realSet(k, v);
      });
    return { release, restore: () => spy.mockRestore() };
  }

  it('leaves a form nobody touched without asking', async () => {
    await render(<NewTaskScreen />);
    expect(mockLeave?.prevent).toBe(false);
  });

  it('asks once the title is changed; Keep editing keeps it, Discard leaves', async () => {
    const tree = await render(<NewTaskScreen />);
    const [input] = byTestId(tree, 'task-title');
    act(() => input.props.onChangeText('Brace the north side today'));
    expect(mockLeave?.prevent).toBe(true);

    tryToLeave();
    expect(lastQuestion()).toMatchObject({
      title: 'tasks.new.leave_title',
      confirmLabel: 'tasks.new.leave_confirm',
      cancelLabel: 'tasks.new.leave_cancel',
    });
    expect(mockDispatch).not.toHaveBeenCalled(); // until Discard
    expect(byTestId(tree, 'task-title')[0].props.value).toBe('Brace the north side today');
    act(() => lastQuestion().onConfirm());
    expect(mockDispatch).toHaveBeenCalledWith(BACK);

    // Typed back to the entry's own line: nothing of the user's to lose.
    act(() => byTestId(tree, 'task-title')[0].props.onChangeText('Engineer asks for extra roof bracing'));
    expect(mockLeave?.prevent).toBe(false);
  });

  it('asks once an assignee or a due date is chosen', async () => {
    const tree = await render(<NewTaskScreen />);
    press(tree, 'assignee-me');
    expect(mockLeave?.prevent).toBe(true);
    press(tree, 'assignee-none');
    expect(mockLeave?.prevent).toBe(false);
    act(() => tree.root.findByType(DueDateField).props.onChange('2026-10-17'));
    expect(mockLeave?.prevent).toBe(true);
  });

  it('while Save’s first write to the phone is in flight: asks, without promising to cancel anything', async () => {
    const write = holdTheWrite();
    try {
      let answer: (t: TaskOut) => void = () => undefined;
      mockCreateTask.mockImplementation(
        () => new Promise<TaskOut>((resolve) => {
          answer = resolve;
        }),
      );
      const tree = await render(<NewTaskScreen />);
      press(tree, 'task-save');
      await flush();
      expect(mockCreateTask).not.toHaveBeenCalled(); // still being written
      expect(mockLeave?.prevent).toBe(true);
      tryToLeave();
      expect(lastQuestion()).toMatchObject({
        title: 'tasks.new.saving_leave_title',
        confirmLabel: 'tasks.new.saving_leave_confirm',
        cancelLabel: 'tasks.new.saving_leave_cancel',
      });

      // On the phone, the request out: leaving loses nothing from here on.
      await act(async () => {
        write.release();
      });
      await flush();
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
      expect(mockLeave?.prevent).toBe(false);

      await act(async () => {
        answer(task());
      });
      await flush();
      expect(mockReplace).toHaveBeenCalledWith('/tasks/task-1');
      expect(mockGuardAtReplace).toEqual([false]);
    } finally {
      write.restore();
    }
  });

  it('a first write that fails keeps asking, even with nothing typed', async () => {
    const write = holdTheWrite(new Error('disk full'));
    try {
      const tree = await render(<NewTaskScreen />);
      press(tree, 'task-save');
      await act(async () => {
        write.release();
      });
      await flush();
      expect(mockCreateTask).not.toHaveBeenCalled();
      const said = tree.root.findAll(
        (n) => typeof n.type === 'string' && n.props.children === 'tasks.new.not_saved',
      );
      expect(said.length).toBeGreaterThan(0);
      expect(mockLeave?.prevent).toBe(true);
      tryToLeave();
      expect(lastQuestion()).toMatchObject({ title: 'tasks.new.leave_title' });
    } finally {
      write.restore();
    }
  });

  it('Save’s own move to the new task is never held up by the question', async () => {
    mockCreateTask.mockResolvedValue(task());
    const tree = await render(<NewTaskScreen />);
    act(() => byTestId(tree, 'task-title')[0].props.onChangeText('Brace the north side today'));
    expect(mockLeave?.prevent).toBe(true);
    press(tree, 'task-save');
    await flush();
    expect(mockReplace).toHaveBeenCalledWith('/tasks/task-1');
    expect(mockGuardAtReplace).toEqual([false]);
  });

  it('a refused save brings its values back, and asks again', async () => {
    mockCreateTask.mockRejectedValue(
      Object.assign(new Error('HTTP 422'), {
        isAxiosError: true,
        response: { status: 422, data: { detail: 'the assignee must be an active user' } },
      }),
    );
    const tree = await render(<NewTaskScreen />);
    press(tree, 'task-save');
    await flush();
    expect(byTestId(tree, 'task-save').length).toBeGreaterThan(0);
    expect(mockLeave?.prevent).toBe(true);
  });

  it('an unresolved create already on the phone: the panel, and no question', async () => {
    await act(async () => {
      await useTaskPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        title: 'Brace the north roof',
        assignee_user_id: null,
        assignee_name: null,
        due_date: null,
        created_at: 1,
      });
    });
    const tree = await render(<NewTaskScreen />);
    await flush();
    expect(byTestId(tree, 'pending-not-found')).toHaveLength(1);
    expect(mockLeave?.prevent).toBe(false);
  });
});