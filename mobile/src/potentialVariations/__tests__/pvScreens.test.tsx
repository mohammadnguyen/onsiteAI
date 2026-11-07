/**
 * The potential-variation screens (DEC-PV-001), rendered for real with
 * react-test-renderer: the flag screen prefills from the entry's first line
 * and pins the version on screen; a "the entry changed" refusal reloads the
 * entry, says so, keeps the typed title and starts a new key; an unresolved
 * flag is asked about by its key before anything is resent; the detail
 * shows the decision only with `permissions.decide`, the builder's note only
 * with `permissions.read_note`, and nothing taken from the entry when the
 * entry is not readable; the entry's section says "not available" on a 404.
 *
 * Lives under src/ because a file under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';

import type { PvDetailOut, PvOut } from '../../api/potentialVariations';

const mockPush = jest.fn();
const mockReplace = jest.fn();
// Whether the leave question was armed at the moment each replace happened.
let mockGuardAtReplace: (boolean | null)[] = [];
let mockParams: Record<string, string> = {};
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
    react.useEffect(() => cb(), []); // eslint-disable-line react-hooks/exhaustive-deps
  },
}));
type MockPrevented = (o: { data: { action: unknown } }) => void;
let mockLeave: { prevent: boolean; onPrevented: MockPrevented } | null = null;
jest.mock('@react-navigation/native', () => ({
  usePreventRemove: (prevent: boolean, onPrevented: MockPrevented) => {
    mockLeave = { prevent, onPrevented };
  },
  useNavigation: () => ({ dispatch: jest.fn() }),
}));
const mockConfirm = jest.fn();
jest.mock('../../siteLog/dialogs', () => ({
  ...jest.requireActual('../../siteLog/dialogs'),
  confirmDestructive: (args: unknown) => mockConfirm(args),
}));
// Keys come back as themselves, with their values appended, so a test can
// see both what was said and what it was said about.
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      o ? `${k}|${Object.entries(o).map(([a, b]) => `${a}=${String(b)}`).join('|')}` : k,
  }),
}));
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  return { ...actual, SafeAreaView: ({ children }: { children: unknown }) => children };
});

// Queries answer from this table, by the first two parts of their key.
type MockResult = { data?: unknown; error?: unknown; isLoading?: boolean };
let mockResults: Record<string, MockResult> = {};
let mockOnInvalidate: (key: unknown[]) => void = () => undefined;
function mockAnswer(queryKey: unknown[]) {
  const r = mockResults[queryKey.slice(0, 2).join(':')] ?? {};
  return {
    isLoading: r.isLoading ?? false,
    // The latest read succeeded. (A read that failed after an earlier one
    // succeeded keeps that data in the real cache: pvCacheAccess.test.tsx
    // covers that with the real QueryClient.)
    isSuccess: !r.error && !(r.isLoading ?? false) && r.data !== undefined,
    data: r.error ? undefined : r.data,
    error: r.error ?? null,
    isError: Boolean(r.error),
    isRefetchError: false,
    isFetching: false,
    refetch: jest.fn(),
  };
}
const mockQueryClient = {
  setQueryData: jest.fn(),
  invalidateQueries: jest.fn(async ({ queryKey }: { queryKey: unknown[] }) => {
    mockOnInvalidate(queryKey);
  }),
  cancelQueries: jest.fn(() => Promise.resolve()),
};
// The list's paged query: what it was asked for, and one page of rows.
type MockInfinite = { queryKey: unknown[]; queryFn: (o: { pageParam: number }) => unknown };
let mockInfiniteCalls: MockInfinite[] = [];
let mockListRows: unknown[] = [];
let mockListError: unknown = null;
jest.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => mockAnswer(queryKey),
  useInfiniteQuery: (o: MockInfinite) => {
    mockInfiniteCalls.push(o);
    return {
      data: mockListError ? undefined : { pages: [mockListRows] },
      error: mockListError,
      isError: Boolean(mockListError),
      isLoading: false,
      isRefetching: false,
      refetch: jest.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: jest.fn(),
    };
  },
  useQueryClient: () => mockQueryClient,
}));

let mockMe = { user_id: 'me-1', full_name: 'Me Myself', role: 'contributor' };
jest.mock('../../api/hooks/useAuth', () => ({ useMe: () => ({ data: mockMe }) }));
jest.mock('../../api/hooks/useJobs', () => ({
  useJobs: () => ({
    data: [{ job_id: 'job-1', job_name: 'Simulated job', job_code: null, status: 'active' }],
  }),
}));
jest.mock('../../siteLog/BackLink', () => ({ BackLink: () => null }));
jest.mock('../../components/JobPickerSheet', () => ({ JobPickerSheet: () => null }));

const mockCreate = jest.fn();
const mockFind = jest.fn();
const mockUpdate = jest.fn();
const mockList = jest.fn();
jest.mock('../../api/potentialVariations', () => ({
  ...jest.requireActual('../../api/potentialVariations'),
  createPotentialVariation: (...a: unknown[]) => mockCreate(...a),
  findMyPvCreate: (...a: unknown[]) => mockFind(...a),
  updatePotentialVariation: (...a: unknown[]) => mockUpdate(...a),
  listPotentialVariations: (...a: unknown[]) => mockList(...a),
}));

import { useAuthStore } from '../../store/auth';
import { usePvPendingCreates } from '../../store/pvPendingCreates';
import { EntryPvSection } from '../EntryPvSection';
import { HomePvCard } from '../HomePvCard';
import { JobPvRow } from '../JobPvRow';
import PotentialVariationDetail from '../../../app/potential-variations/[id]';
import PotentialVariationsList from '../../../app/potential-variations/index';
import NewPotentialVariationScreen from '../../../app/potential-variations/new';

const KITCHEN = 'Owner asked on site for two extra power points in the kitchen';
const SOURCE_CHANGED =
  'source_changed: the entry has changed since it was opened; check it and confirm again';

function entry(revisionNo: number, body: string | null, extra: Record<string, unknown> = {}) {
  return {
    site_log_event_id: 'event-1',
    job_id: 'job-1',
    revision: { revision_no: revisionNo, body_text: body, withdrawn: false, ...extra },
  };
}

function pv(overrides: Partial<PvOut> = {}): PvOut {
  return {
    potential_variation_id: 'pv-1',
    title: KITCHEN,
    review_state: 'to_check',
    ended_reason: null,
    decision_note: null,
    origin: 'manual',
    job: { job_id: 'job-1', job_name: 'Simulated job', job_code: null, status: 'active' },
    created_by: { user_id: 'me-1', full_name: 'Me Myself' },
    decided_by: null,
    decided_at: null,
    source: { site_log_event_id: 'event-1', revision_no: 1, readable: true, job_id: 'job-1' },
    permissions: { decide: false, read_note: false, set_job: false, change_job: false },
    created_at: '2026-11-07T00:00:00Z',
    updated_at: '2026-11-07T00:00:00Z',
    ...overrides,
  };
}

function detail(overrides: Partial<PvDetailOut> = {}): PvDetailOut {
  const base = pv();
  return {
    ...base,
    source: {
      ...base.source,
      pinned: {
        revision_no: 1,
        body_text: `${KITCHEN}\nNear the island bench`,
        internal_location: 'Kitchen',
        occurred_at: null,
        created_at: '2026-11-07T00:00:00Z',
      },
      current_revision_no: 1,
      current_withdrawn: false,
      current_attachments: [
        { declared_media_type: 'image', state: 'stored' },
        { declared_media_type: 'audio', state: 'stored' },
      ],
    },
    ...overrides,
  } as PvDetailOut;
}

const ADMIN_PERMISSIONS = { decide: true, read_note: true, set_job: false, change_job: true };

const byTestId = (tree: ReactTestRenderer, id: string): ReactTestInstance[] =>
  tree.root.findAll((n) => n.props.testID === id && typeof n.type !== 'string');

/** Every string the screen renders. */
const allText = (tree: ReactTestRenderer): string =>
  tree.root
    .findAll((n) => typeof n.type === 'string')
    .flatMap((n) => n.children.filter((c): c is string => typeof c === 'string'))
    .join('\n');

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

function type(tree: ReactTestRenderer, id: string, text: string) {
  const [node] = byTestId(tree, id);
  if (!node) throw new Error(`no element ${id}`);
  act(() => node.props.onChangeText(text));
}

const sentBodies = () =>
  mockCreate.mock.calls.map(
    (c) => c[1] as { create_client_id: string; title: string; source_revision_no: number },
  );

beforeEach(() => {
  jest.clearAllMocks();
  mockLeave = null;
  mockGuardAtReplace = [];
  mockParams = {};
  mockResults = {};
  mockOnInvalidate = () => undefined;
  mockInfiniteCalls = [];
  mockListRows = [];
  mockListError = null;
  mockMe = { user_id: 'me-1', full_name: 'Me Myself', role: 'contributor' };
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
  usePvPendingCreates.setState({ pending: [], sending: [], hydrated: true, loadFailed: false });
  useAuthStore.setState({ userId: 'me-1', sessionNonce: 1, accessToken: 'token-me' });
});

describe('flagging an entry', () => {
  beforeEach(() => {
    mockParams = { eventId: 'event-1' };
    mockResults['site-log:event'] = { data: entry(3, `${KITCHEN}\nNear the island bench`) };
    mockFind.mockResolvedValue(null);
  });

  it('prefills the title from the first line, shows the version, and pins the version shown', async () => {
    let finish: (p: PvOut) => void = () => undefined;
    mockCreate.mockImplementation(
      () => new Promise<PvOut>((resolve) => {
        finish = resolve;
      }),
    );
    const tree = await render(<NewPotentialVariationScreen />);
    expect(byTestId(tree, 'pv-title')[0].props.value).toBe(KITCHEN);
    expect(allText(tree)).toContain('potentialVariations.new.version|n=3');
    expect(allText(tree)).toContain('potentialVariations.disclaimer');
    expect(allText(tree)).toContain('potentialVariations.new.job_from_entry|job=Simulated job');
    // An edited title is the user's own: leaving would lose it, so it asks.
    type(tree, 'pv-title', '  Two extra power points, kitchen  ');
    expect(mockLeave?.prevent).toBe(true);

    press(tree, 'pv-save');
    await flush();
    press(tree, 'pv-save'); // a double tap while it is out sends nothing more
    await flush();
    expect(mockCreate).toHaveBeenCalledTimes(1);
    expect(byTestId(tree, 'pv-save')[0].props.disabled).toBe(true);
    expect(mockCreate).toHaveBeenCalledWith('event-1', {
      create_client_id: expect.any(String),
      title: 'Two extra power points, kitchen',
      source_revision_no: 3,
    });
    // The record is on the phone while the request is out.
    expect(usePvPendingCreates.getState().forEvent('me-1', 'event-1')).toMatchObject({
      source_revision_no: 3,
    });

    await act(async () => {
      finish(pv());
    });
    await flush();
    expect(mockReplace).toHaveBeenCalledWith('/potential-variations/pv-1');
    // Save's own move is never held up by the leave question.
    expect(mockGuardAtReplace).toEqual([false]);
    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['potential-variations'],
    });
    expect(usePvPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
  });

  it('a voice-only entry starts with an empty title the user types', async () => {
    mockResults['site-log:event'] = { data: entry(1, null) };
    mockCreate.mockResolvedValue(pv());
    const tree = await render(<NewPotentialVariationScreen />);
    expect(byTestId(tree, 'pv-title')[0].props.value).toBe('');
    expect(byTestId(tree, 'pv-save')[0].props.disabled).toBe(true);
    type(tree, 'pv-title', 'Extra power points');
    expect(byTestId(tree, 'pv-save')[0].props.disabled).toBe(false);
    press(tree, 'pv-save');
    await flush();
    expect(sentBodies()[0]).toMatchObject({ title: 'Extra power points', source_revision_no: 1 });
  });

  it('the entry changed: it is reloaded, the user is asked to check again, the title is kept, and a new key is used', async () => {
    mockCreate.mockRejectedValueOnce(
      Object.assign(new Error('HTTP 409'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: SOURCE_CHANGED } },
      }),
    );
    // Reading the entry again finds its newer version.
    mockOnInvalidate = (key) => {
      if (key[0] === 'site-log') {
        mockResults['site-log:event'] = { data: entry(4, 'Owner now wants four power points') };
      }
    };
    const tree = await render(<NewPotentialVariationScreen />);
    type(tree, 'pv-title', 'Extra power points, kitchen');
    press(tree, 'pv-save');
    await flush();

    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['site-log', 'event', 'event-1'],
      exact: true,
    });
    expect(allText(tree)).toContain('potentialVariations.new.source_changed');
    expect(allText(tree)).toContain('potentialVariations.new.version|n=4');
    expect(allText(tree)).toContain('Owner now wants four power points');
    expect(byTestId(tree, 'pv-title')[0].props.value).toBe('Extra power points, kitchen');
    // Nothing was created under the refused key, and nothing of it is kept.
    expect(usePvPendingCreates.getState().forEvent('me-1', 'event-1')).toBeUndefined();
    // The values exist only on this screen now: leaving asks first.
    expect(mockLeave?.prevent).toBe(true);

    mockCreate.mockResolvedValueOnce(pv({ potential_variation_id: 'pv-2' }));
    press(tree, 'pv-save');
    await flush();
    const [first, second] = sentBodies();
    expect(first.source_revision_no).toBe(3);
    expect(second.source_revision_no).toBe(4); // the version now on screen
    expect(second.title).toBe('Extra power points, kitchen');
    expect(second.create_client_id).not.toBe(first.create_client_id);
    expect(mockReplace).toHaveBeenCalledWith('/potential-variations/pv-2');
  });

  it('an unknown result keeps the record; reopening asks by that key before anything is resent, then sends the same values', async () => {
    const offline = () => Object.assign(new Error('Network Error'), { isAxiosError: true });
    mockCreate.mockRejectedValueOnce(offline());
    const first = await render(<NewPotentialVariationScreen />);
    press(first, 'pv-save');
    await flush();
    const kept = usePvPendingCreates.getState().forEvent('me-1', 'event-1');
    expect(kept).toMatchObject({ title: KITCHEN, source_revision_no: 3 });
    expect(mockLeave?.prevent).toBe(false); // on the phone: leaving loses nothing
    act(() => first.unmount());
    mounted.splice(mounted.indexOf(first), 1);

    mockFind.mockResolvedValue(null);
    mockCreate.mockResolvedValueOnce(pv());
    const reopened = await render(<NewPotentialVariationScreen />);
    await flush();
    expect(mockFind).toHaveBeenCalledWith(kept?.create_client_id);
    expect(byTestId(reopened, 'pv-pending-not-found')).toHaveLength(1);
    expect(byTestId(reopened, 'pv-save')).toHaveLength(0); // no second flag offered
    expect(mockCreate).toHaveBeenCalledTimes(1);

    press(reopened, 'pv-pending-send-again');
    await flush();
    const [lost, resent] = mockCreate.mock.calls;
    expect(resent).toEqual(lost); // same entry, same key, same title, same version
    expect(mockReplace).toHaveBeenCalledWith('/potential-variations/pv-1');
  });

  it('an unresolved flag the server already has opens it', async () => {
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 2,
        title: 'x',
        created_at: 1,
      });
    });
    mockFind.mockResolvedValue(pv({ potential_variation_id: 'pv-9' }));
    await render(<NewPotentialVariationScreen />);
    await flush();
    expect(mockCreate).not.toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/potential-variations/pv-9?notice=already_created');
  });

  it('an unresolved flag whose entry this user can no longer read: kept and asked about, its saved title not shown', async () => {
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 3,
        title: KITCHEN,
        created_at: 1,
      });
    });
    mockResults['site-log:event'] = {
      error: Object.assign(new Error('HTTP 404'), {
        isAxiosError: true,
        response: { status: 404, data: { detail: 'Site log event not found' } },
      }),
    };
    const tree = await render(<NewPotentialVariationScreen />);
    await flush();
    expect(mockFind).toHaveBeenCalledWith('lost-key');
    expect(byTestId(tree, 'pv-pending-not-found')).toHaveLength(1);
    expect(allText(tree)).not.toContain(KITCHEN);
    expect(allText(tree)).toContain('potentialVariations.placeholder_title');
    // The record itself - key and values - is untouched.
    expect(usePvPendingCreates.getState().forEvent('me-1', 'event-1')).toMatchObject({
      create_client_id: 'lost-key',
      title: KITCHEN,
      source_revision_no: 3,
    });
  });

  it('an unresolved flag on an entry this user can read shows its saved title', async () => {
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 3,
        title: 'Two extra GPOs',
        created_at: 1,
      });
    });
    const tree = await render(<NewPotentialVariationScreen />);
    await flush();
    expect(byTestId(tree, 'pv-pending-not-found')).toHaveLength(1);
    expect(allText(tree)).toContain('Two extra GPOs');
  });

  it('a route-less server keeps the flag on the phone and says so', async () => {
    const routeMissing = () =>
      Object.assign(new Error('HTTP 404'), {
        isAxiosError: true,
        response: { status: 404, data: { detail: 'Not Found' } },
      });
    mockCreate.mockRejectedValueOnce(routeMissing());
    mockFind.mockRejectedValue(routeMissing());
    const tree = await render(<NewPotentialVariationScreen />);
    press(tree, 'pv-save');
    await flush();
    expect(byTestId(tree, 'pv-pending-unavailable')).toHaveLength(1);
    expect(usePvPendingCreates.getState().forEvent('me-1', 'event-1')).toMatchObject({
      title: KITCHEN,
      source_revision_no: 3,
    });
  });

  it('a withdrawn entry offers no flag', async () => {
    mockResults['site-log:event'] = { data: entry(2, KITCHEN, { withdrawn: true }) };
    const tree = await render(<NewPotentialVariationScreen />);
    expect(byTestId(tree, 'pv-save')).toHaveLength(0);
    expect(allText(tree)).toContain('potentialVariations.new.entry_withdrawn');
  });

  it('asks before leaving once the title is changed', async () => {
    const tree = await render(<NewPotentialVariationScreen />);
    expect(mockLeave?.prevent).toBe(false);
    type(tree, 'pv-title', 'Something else');
    expect(mockLeave?.prevent).toBe(true);
    act(() => mockLeave?.onPrevented({ data: { action: { type: 'GO_BACK' } } }));
    expect(mockConfirm.mock.calls[0][0]).toMatchObject({ title: 'potentialVariations.new.leave_title' });
  });
});

describe('a flag’s detail', () => {
  beforeEach(() => {
    mockParams = { id: 'pv-1' };
  });

  it('the flagger: the original record and today’s attachments, no decision, no note', async () => {
    mockResults['potential-variations:detail'] = {
      data: detail({ decision_note: 'should never show', decided_by: null }),
    };
    const tree = await render(<PotentialVariationDetail />);
    const text = allText(tree);
    expect(text).toContain(KITCHEN);
    expect(text).toContain('Near the island bench');
    expect(text).toContain('potentialVariations.disclaimer');
    expect(text).toMatch(/potentialVariations\.detail\.original\|n=1\|time=/);
    expect(text).toContain('potentialVariations.detail.attachments_now|list=siteLog.list.count_image|count=1');
    expect(byTestId(tree, 'pv-decision-editor')).toHaveLength(0);
    expect(byTestId(tree, 'pv-decide:ended')).toHaveLength(0);
    expect(byTestId(tree, 'pv-note')).toHaveLength(0);
    expect(text).not.toContain('should never show');
    press(tree, 'pv-open-entry');
    expect(mockPush).toHaveBeenCalledWith('/site-log/event-1');
  });

  it('says when the entry has a newer version or was withdrawn since', async () => {
    const d = detail();
    mockResults['potential-variations:detail'] = {
      data: { ...d, source: { ...d.source, current_revision_no: 3, current_withdrawn: true } },
    };
    const tree = await render(<PotentialVariationDetail />);
    expect(allText(tree)).toContain('potentialVariations.detail.newer_version|n=3');
    expect(allText(tree)).toContain('potentialVariations.detail.withdrawn');
    // The pinned text is still the version that was flagged.
    expect(allText(tree)).toContain('Near the island bench');
  });

  it('an entry this user may not read: no entry text, no attachment summary, no way in - even if sent', async () => {
    const d = detail({ decision_note: null });
    // As the server sends it (nulls) - and, defensively, as it must never
    // be shown even if something taken from the entry did arrive.
    for (const source of [
      {
        site_log_event_id: 'event-1',
        revision_no: 1,
        readable: false,
        job_id: null,
        pinned: null,
        current_revision_no: null,
        current_withdrawn: null,
        current_attachments: null,
      },
      { ...d.source, readable: false },
    ]) {
      mockResults['potential-variations:detail'] = {
        data: { ...d, title: source.pinned ? KITCHEN : null, source },
      };
      const tree = await render(<PotentialVariationDetail />);
      const text = allText(tree);
      expect(text).toContain('potentialVariations.placeholder_title');
      expect(text).not.toContain('power points');
      expect(text).not.toContain('island');
      expect(text).not.toContain('Kitchen');
      expect(text).not.toContain('attachments_now');
      expect(byTestId(tree, 'pv-open-entry')).toHaveLength(0);
      expect(byTestId(tree, 'pv-source-hidden')).toHaveLength(1);
      act(() => tree.unmount());
      mounted.splice(mounted.indexOf(tree), 1);
    }
  });

  it('the builder: the note, the decision controls, and an end with a reason', async () => {
    mockMe = { user_id: 'boss', full_name: 'Boss', role: 'admin' };
    const current = detail({
      review_state: 'following_up',
      decision_note: 'Client to confirm',
      decided_by: { user_id: 'boss', full_name: 'Boss' },
      decided_at: '2026-11-07T01:00:00Z',
      permissions: ADMIN_PERMISSIONS,
    });
    mockResults['potential-variations:detail'] = { data: current };
    const answered = detail({
      review_state: 'ended',
      ended_reason: 'other',
      decision_note: 'Client changed mind',
      permissions: ADMIN_PERMISSIONS,
    });
    mockUpdate.mockResolvedValue(answered);
    const tree = await render(<PotentialVariationDetail />);
    expect(byTestId(tree, 'pv-note')[0].props.children).toBe('Client to confirm');
    expect(byTestId(tree, 'pv-decide:to_check').length).toBeGreaterThan(0);

    press(tree, 'pv-decide:ended');
    // Ending needs a reason first.
    expect(byTestId(tree, 'pv-decision-save')[0].props.disabled).toBe(true);
    press(tree, 'pv-reason:other');
    // The note field starts with the current note: what is saved is what is shown.
    expect(byTestId(tree, 'pv-decision-note')[0].props.value).toBe('Client to confirm');
    type(tree, 'pv-decision-note', '');
    // "Other" needs a note.
    expect(byTestId(tree, 'pv-decision-save')[0].props.disabled).toBe(true);
    type(tree, 'pv-decision-note', 'x'.repeat(501));
    expect(byTestId(tree, 'pv-decision-save')[0].props.disabled).toBe(true);
    type(tree, 'pv-decision-note', '  Client changed mind  ');
    expect(byTestId(tree, 'pv-decision-save')[0].props.disabled).toBe(false);

    press(tree, 'pv-decision-save');
    await flush();
    expect(mockUpdate).toHaveBeenCalledWith('pv-1', {
      decision: { review_state: 'ended', ended_reason: 'other', note: 'Client changed mind' },
    });
    // The answer goes into the cached detail; the lists hear of it.
    expect(mockQueryClient.setQueryData).toHaveBeenCalledWith(
      ['potential-variations', 'detail', 'pv-1'],
      answered,
    );
    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['potential-variations', 'list'],
    });
    expect(byTestId(tree, 'pv-decision-editor')).toHaveLength(0);
  });

  it('the builder moving a flag back: no reason is sent, and the note shown is the note saved', async () => {
    mockResults['potential-variations:detail'] = {
      data: detail({
        review_state: 'ended',
        ended_reason: 'already_in_contract',
        decision_note: null,
        permissions: ADMIN_PERMISSIONS,
      }),
    };
    mockUpdate.mockResolvedValue(detail({ permissions: ADMIN_PERMISSIONS }));
    const tree = await render(<PotentialVariationDetail />);
    expect(allText(tree)).toContain('potentialVariations.reason.already_in_contract');
    press(tree, 'pv-decide:to_check');
    press(tree, 'pv-decision-save');
    await flush();
    expect(mockUpdate).toHaveBeenCalledWith('pv-1', {
      decision: { review_state: 'to_check', ended_reason: null, note: null },
    });
  });

  it('a flag with no job asks for one, and the flagger may set it', async () => {
    mockResults['potential-variations:detail'] = {
      data: detail({
        job: null,
        permissions: { decide: false, read_note: false, set_job: true, change_job: false },
      }),
    };
    const tree = await render(<PotentialVariationDetail />);
    expect(byTestId(tree, 'pv-job-prompt')).toHaveLength(1);
    expect(byTestId(tree, 'pv-job-pick').length).toBeGreaterThan(0);
    expect(allText(tree)).toContain('potentialVariations.detail.set_job');
  });
});

describe('the entry’s section', () => {
  it('lists the flags that may be seen and offers to flag', async () => {
    mockResults['potential-variations:event'] = {
      data: [pv(), pv({ potential_variation_id: 'pv-2', title: null, source: { ...pv().source, readable: false } })],
    };
    const tree = await render(<EntryPvSection eventId="event-1" withdrawn={false} />);
    expect(allText(tree)).toContain(KITCHEN);
    expect(allText(tree)).toContain('potentialVariations.placeholder_title');
    press(tree, 'entry-pv-flag');
    expect(mockPush).toHaveBeenCalledWith('/potential-variations/new?eventId=event-1');
  });

  it('a server without flags: a quiet note, the kept flag mentioned, no way to flag', async () => {
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 1,
        title: 'Extra power points',
        created_at: 1,
      });
    });
    mockResults['potential-variations:event'] = {
      error: Object.assign(new Error('HTTP 404'), {
        isAxiosError: true,
        response: { status: 404, data: { detail: 'Not Found' } },
      }),
    };
    const tree = await render(<EntryPvSection eventId="event-1" withdrawn={false} />);
    expect(byTestId(tree, 'entry-pv-unavailable')).toHaveLength(1);
    expect(byTestId(tree, 'entry-pv-pending-kept')).toHaveLength(1);
    expect(byTestId(tree, 'entry-pv-flag')).toHaveLength(0);
  });

  it('a withdrawn entry, or one with a flag in doubt, offers no new flag', async () => {
    mockResults['potential-variations:event'] = { data: [] };
    const withdrawn = await render(<EntryPvSection eventId="event-1" withdrawn />);
    expect(byTestId(withdrawn, 'entry-pv-flag')).toHaveLength(0);
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 1,
        title: 'x',
        created_at: 1,
      });
    });
    const inDoubt = await render(<EntryPvSection eventId="event-1" withdrawn={false} />);
    expect(byTestId(inDoubt, 'entry-pv-flag')).toHaveLength(0);
    expect(byTestId(inDoubt, 'entry-pv-pending').length).toBeGreaterThan(0);
  });
});

describe('counts on Home and on a job', () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => pv({ potential_variation_id: `pv-${i}` }));

  it('Home: a count to check, "50+" for a full page; nothing for none or for a failed read', async () => {
    mockResults['potential-variations:count'] = { data: many(3) };
    let tree = await render(<HomePvCard />);
    expect(allText(tree)).toContain('potentialVariations.home.to_check|n=3');
    press(tree, 'home-pv-to-check');
    expect(mockPush).toHaveBeenCalledWith('/potential-variations');

    mockResults['potential-variations:count'] = { data: many(50) };
    tree = await render(<HomePvCard />);
    expect(allText(tree)).toContain('potentialVariations.home.to_check|n=50+');

    mockResults['potential-variations:count'] = { data: [] };
    tree = await render(<HomePvCard />);
    expect(byTestId(tree, 'home-pv-to-check')).toHaveLength(0);

    mockResults['potential-variations:count'] = {
      error: Object.assign(new Error('HTTP 404'), { isAxiosError: true, response: { status: 404 } }),
    };
    tree = await render(<HomePvCard />);
    expect(byTestId(tree, 'home-pv-to-check')).toHaveLength(0);
  });

  it('a job: its count opens the list for that job in every state; hidden when it cannot be read', async () => {
    mockResults['potential-variations:count'] = { data: many(2) };
    let tree = await render(<JobPvRow jobId="job-1" />);
    expect(byTestId(tree, 'job-pv-count')[0].props.children).toBe('2');
    press(tree, 'job-pv-row');
    expect(mockPush).toHaveBeenCalledWith('/potential-variations?jobId=job-1&state=all');

    mockResults['potential-variations:count'] = {
      error: Object.assign(new Error('Network Error'), { isAxiosError: true }),
    };
    tree = await render(<JobPvRow jobId="job-1" />);
    expect(byTestId(tree, 'job-pv-row')).toHaveLength(0);
  });
});

describe('the list', () => {
  const lastAsked = () => mockInfiniteCalls[mockInfiniteCalls.length - 1];

  it('opens on To check and asks for exactly that', async () => {
    mockListRows = [pv()];
    const tree = await render(<PotentialVariationsList />);
    expect(lastAsked().queryKey).toEqual(['potential-variations', 'list', 'to_check', null]);
    await lastAsked().queryFn({ pageParam: 0 });
    expect(mockList).toHaveBeenLastCalledWith({ review_state: 'to_check', job_id: undefined, offset: 0 });
    expect(byTestId(tree, 'pv-list-job')).toHaveLength(0);
    expect(allText(tree)).toContain(KITCHEN);
    expect(allText(tree)).toContain('Simulated job');
    expect(allText(tree)).toMatch(/potentialVariations\.flagged_by\|name=Me Myself\|date=/);
    press(tree, 'pv-row:pv-1');
    expect(mockPush).toHaveBeenCalledWith('/potential-variations/pv-1');
  });

  it('from a job: narrowed to that job in every state, with a way back to all jobs', async () => {
    mockParams = { jobId: 'job-1', state: 'all' };
    mockListRows = [
      pv({
        potential_variation_id: 'pv-2',
        title: KITCHEN, // never shown: the entry is not readable for this user
        job: null,
        source: { site_log_event_id: 'event-2', revision_no: 1, readable: false, job_id: null },
      }),
    ];
    const tree = await render(<PotentialVariationsList />);
    expect(lastAsked().queryKey).toEqual(['potential-variations', 'list', 'all', 'job-1']);
    await lastAsked().queryFn({ pageParam: 20 });
    expect(mockList).toHaveBeenLastCalledWith({ review_state: undefined, job_id: 'job-1', offset: 20 });
    expect(allText(tree)).toContain('potentialVariations.list.job_filter|job=Simulated job');
    expect(allText(tree)).toContain('potentialVariations.placeholder_title');
    expect(allText(tree)).toContain('potentialVariations.list.no_job');
    expect(allText(tree)).not.toContain('power points');

    press(tree, 'pv-list-clear-job');
    expect(lastAsked().queryKey).toEqual(['potential-variations', 'list', 'all', null]);
    press(tree, 'pv-filter:ended');
    expect(lastAsked().queryKey).toEqual(['potential-variations', 'list', 'ended', null]);
  });

  it('says it could not load - never "none" - when the read fails', async () => {
    mockListError = Object.assign(new Error('Network Error'), { isAxiosError: true });
    const tree = await render(<PotentialVariationsList />);
    expect(byTestId(tree, 'pv-list-error')).toHaveLength(1);
    expect(byTestId(tree, 'pv-list-empty')).toHaveLength(0);
  });
});
