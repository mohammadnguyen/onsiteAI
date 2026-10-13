/**
 * Task detail with the REAL query cache: two saves of one task from two
 * screens, answered out of order, must end on what the server holds - not
 * on whichever answer happened to arrive last. A fake server applies each
 * save when the test says, and its answer reaches the phone when the test
 * says; a read returns what the server holds at that moment.
 *
 * Lives under src/ because a file under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { TaskOut, TaskPatch } from '../../api/tasks';

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn(), back: jest.fn(), canGoBack: () => true },
  useLocalSearchParams: () => ({ id: 'task-1' }),
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const react = require('react') as typeof import('react');
    react.useEffect(() => cb(), []); // eslint-disable-line react-hooks/exhaustive-deps
  },
}));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  return { ...actual, SafeAreaView: ({ children }: { children: unknown }) => children };
});
jest.mock('@react-native-community/datetimepicker', () => ({ __esModule: true, default: () => null }));
jest.mock('../../api/hooks/useAuth', () => ({
  useMe: () => ({ data: { user_id: 'me-1', full_name: 'Me Myself', role: 'contributor' } }),
}));
jest.mock('../../api/hooks/useJobs', () => ({ useJobs: () => ({ data: [] }) }));
jest.mock('../../api/hooks/useUsers', () => ({ useUsers: () => ({ data: [] }) }));
jest.mock('../../siteLog/BackLink', () => ({ BackLink: () => null }));
jest.mock('../../components/JobPickerSheet', () => ({ JobPickerSheet: () => null }));

// ---- the fake server ------------------------------------------------------
type Save = { patch: TaskPatch; answer?: TaskOut; deliver: (t: TaskOut) => void };
let mockServer: TaskOut;
const mockSaves: Save[] = [];
jest.mock('../../api/tasks', () => ({
  TASK_STATUSES: ['to_do', 'in_progress', 'blocked', 'done'],
  getTask: jest.fn(async () => ({ ...mockServer })),
  patchTask: jest.fn(
    (_id: string, patch: TaskPatch) =>
      new Promise<TaskOut>((resolve) => {
        mockSaves.push({ patch, deliver: resolve });
      }),
  ),
}));

import { useAuthStore } from '../../store/auth';
import { TaskStatusChips } from '../TaskStatusChips';
import TaskDetail from '../../../app/tasks/[id]';

function task(overrides: Partial<TaskOut> = {}): TaskOut {
  return {
    task_id: 'task-1',
    title: 'Add roof bracing',
    status: 'to_do',
    origin: 'manual',
    due_date: null,
    job: null,
    assignee: null,
    created_by: { user_id: 'me-1', full_name: 'Me Myself' },
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

/** The server applies save `i` now, and keeps the answer it will send. */
function serverApplies(i: number): void {
  mockServer = { ...mockServer, ...(mockSaves[i].patch as Partial<TaskOut>) };
  mockSaves[i].answer = { ...mockServer };
}

/** Save `i`'s answer reaches the phone now. */
async function answerArrives(i: number): Promise<void> {
  await act(async () => {
    mockSaves[i].deliver(mockSaves[i].answer as TaskOut);
  });
  await flush();
}

async function flush() {
  for (let i = 0; i < 8; i += 1) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const byTestId = (tree: ReactTestRenderer, id: string): ReactTestInstance[] =>
  tree.root.findAll((n) => n.props.testID === id && typeof n.type !== 'string');

function press(tree: ReactTestRenderer, id: string) {
  const [node] = byTestId(tree, id);
  if (!node) throw new Error(`no element ${id}`);
  act(() => {
    node.props.onPress();
  });
}

let client: QueryClient;
const open: ReactTestRenderer[] = [];
async function openTask(): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(
      <QueryClientProvider client={client}>
        <TaskDetail />
      </QueryClientProvider>,
    );
  });
  open.push(tree as ReactTestRenderer);
  await flush();
  return tree as ReactTestRenderer;
}

function close(tree: ReactTestRenderer) {
  open.splice(open.indexOf(tree), 1);
  act(() => tree.unmount());
}

beforeEach(() => {
  mockSaves.length = 0;
  mockServer = task();
  useAuthStore.setState({ userId: 'me-1', sessionNonce: 1, accessToken: 'token-me' });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});

afterEach(() => {
  while (open.length > 0) {
    const tree = open.pop();
    act(() => tree?.unmount());
  }
  client.clear();
});

const shownStatus = (tree: ReactTestRenderer) => tree.root.findByType(TaskStatusChips).props.value;

it('two saves from two screens, answered out of order, end on what the server holds', async () => {
  const first = await openTask();
  expect(shownStatus(first)).toBe('to_do');
  press(first, 'task-status:in_progress'); // save 0
  await flush();
  close(first); // the user goes back; that save is still out

  const second = await openTask(); // and opens the task again
  // A title is being typed here meanwhile; no save may throw it away.
  press(second, 'task-title-edit');
  const [input] = byTestId(second, 'task-title-input');
  act(() => input.props.onChangeText('Brace the north side today'));
  press(second, 'task-status:done'); // save 1
  await flush();

  // The server takes them in the order they were sent: Done is the last word.
  serverApplies(0);
  serverApplies(1);
  // The answers arrive the other way round: Done first, then In progress.
  await answerArrives(1);
  await answerArrives(0);

  expect(mockServer.status).toBe('done');
  expect(shownStatus(second)).toBe('done');
  expect(client.getQueryData<TaskOut>(['tasks', 'detail', 'task-1'])?.status).toBe('done');
  expect(byTestId(second, 'task-title-input')[0].props.value).toBe('Brace the north side today');
});

it('answered in order, the screen ends on the same state', async () => {
  const tree = await openTask();
  press(tree, 'task-status:in_progress');
  await flush();
  serverApplies(0);
  await answerArrives(0);
  press(tree, 'task-status:blocked');
  await flush();
  serverApplies(1);
  await answerArrives(1);
  expect(shownStatus(tree)).toBe('blocked');
  expect(mockServer.status).toBe('blocked');
});
