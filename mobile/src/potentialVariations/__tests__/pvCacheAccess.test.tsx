/**
 * Potential variations with the REAL query cache (DEC-PV-001 source rules).
 *
 * The query cache keeps an earlier read's data when a later read fails. Once
 * the server has said no (404), none of that may stay on screen: not the
 * flag, not the entry's text, not the builder's note, not a pending flag's
 * saved title (it was taken from the entry's text). And a first read that
 * fails offers a way to read again - automatic retries are off.
 *
 * pvScreens.test.tsx mocks the query hooks, which cannot show any of this;
 * here a fake server answers through the real QueryClient.
 *
 * Lives under src/ because a file under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { PvDetailOut } from '../../api/potentialVariations';

let mockParams: Record<string, string> = {};
jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn(), back: jest.fn(), canGoBack: () => true },
  useLocalSearchParams: () => mockParams,
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const react = require('react') as typeof import('react');
    react.useEffect(() => cb(), []); // eslint-disable-line react-hooks/exhaustive-deps
  },
}));
jest.mock('@react-navigation/native', () => ({
  usePreventRemove: () => undefined,
  useNavigation: () => ({ dispatch: jest.fn() }),
}));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  return { ...actual, SafeAreaView: ({ children }: { children: unknown }) => children };
});
jest.mock('../../api/hooks/useAuth', () => ({
  useMe: () => ({ data: { user_id: 'me-1', full_name: 'Me Myself', role: 'admin' } }),
}));
jest.mock('../../api/hooks/useJobs', () => ({ useJobs: () => ({ data: [] }) }));
jest.mock('../../siteLog/BackLink', () => ({ BackLink: () => null }));
jest.mock('../../components/JobPickerSheet', () => ({ JobPickerSheet: () => null }));

// ---- the fake server ------------------------------------------------------
const mockGetPv = jest.fn();
const mockListEvent = jest.fn();
const mockFind = jest.fn();
jest.mock('../../api/potentialVariations', () => ({
  ...jest.requireActual('../../api/potentialVariations'),
  getPotentialVariation: (...a: unknown[]) => mockGetPv(...a),
  listEventPotentialVariations: (...a: unknown[]) => mockListEvent(...a),
  findMyPvCreate: (...a: unknown[]) => mockFind(...a),
  createPotentialVariation: jest.fn(),
  updatePotentialVariation: jest.fn(),
}));
const mockGetEvent = jest.fn();
jest.mock('../../api/siteLog', () => ({
  getEvent: (...a: unknown[]) => mockGetEvent(...a),
}));

import { useAuthStore } from '../../store/auth';
import { usePvPendingCreates } from '../../store/pvPendingCreates';
import { EntryPvSection } from '../EntryPvSection';
import PotentialVariationDetail from '../../../app/potential-variations/[id]';
import NewPotentialVariationScreen from '../../../app/potential-variations/new';

const TITLE = 'Two extra power points, kitchen';
const SECOND_LINE = 'Near the island bench';
const PINNED = `Owner asked on site for two extra power points in the kitchen\n${SECOND_LINE}`;
const NOTE = 'E2 shows 6 GPOs in kitchen';
const DETAIL_KEY = ['potential-variations', 'detail', 'pv-1'];
const ENTRY_KEY = ['site-log', 'event', 'event-1'];

function httpError(status: number, detail: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  });
}
const noSignal = () =>
  Object.assign(new Error('Network Error'), { isAxiosError: true, code: 'ERR_NETWORK' });

/** What the builder sees: the title, the pinned text and the note. */
function builderView(): PvDetailOut {
  return {
    potential_variation_id: 'pv-1',
    title: TITLE,
    review_state: 'following_up',
    ended_reason: null,
    decision_note: NOTE,
    origin: 'manual',
    job: null,
    created_by: { user_id: 'someone-else', full_name: 'Site Worker' },
    decided_by: { user_id: 'me-1', full_name: 'Me Myself' },
    decided_at: '2026-11-07T01:00:00Z',
    source: {
      site_log_event_id: 'event-1',
      revision_no: 1,
      readable: true,
      job_id: null,
      pinned: {
        revision_no: 1,
        body_text: PINNED,
        internal_location: null,
        occurred_at: null,
        created_at: '2026-11-07T00:00:00Z',
      },
      current_revision_no: 1,
      current_withdrawn: false,
      current_attachments: [],
    },
    permissions: { decide: true, read_note: true, set_job: true, change_job: false },
    created_at: '2026-11-07T00:00:00Z',
    updated_at: '2026-11-07T01:00:00Z',
  } as PvDetailOut;
}

const ENTRY = {
  site_log_event_id: 'event-1',
  job_id: null,
  revision: { revision_no: 1, body_text: PINNED, withdrawn: false },
};

const byTestId = (tree: ReactTestRenderer, id: string): ReactTestInstance[] =>
  tree.root.findAll((n) => n.props.testID === id && typeof n.type !== 'string');

const allText = (tree: ReactTestRenderer): string =>
  tree.root
    .findAll((n) => typeof n.type === 'string')
    .flatMap((n) => n.children.filter((c): c is string => typeof c === 'string'))
    .join('\n');

async function flush() {
  for (let i = 0; i < 8; i += 1) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

let client: QueryClient;
const mounted: ReactTestRenderer[] = [];
async function render(el: React.ReactElement): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(<QueryClientProvider client={client}>{el}</QueryClientProvider>);
  });
  mounted.push(tree as ReactTestRenderer);
  await flush();
  return tree as ReactTestRenderer;
}

function press(tree: ReactTestRenderer, id: string) {
  const [node] = byTestId(tree, id);
  if (!node) throw new Error(`no element ${id}`);
  act(() => {
    node.props.onPress();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  useAuthStore.setState({ userId: 'me-1', sessionNonce: 1, accessToken: 'token-me' });
  usePvPendingCreates.setState({ pending: [], sending: [], hydrated: true, loadFailed: false });
  (globalThis as unknown as { __asyncStorageData?: Map<string, string> }).__asyncStorageData?.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});

afterEach(() => {
  while (mounted.length > 0) {
    const tree = mounted.pop();
    act(() => tree?.unmount());
  }
  client.clear();
});

describe('the detail screen', () => {
  beforeEach(() => {
    mockParams = { id: 'pv-1' };
  });

  it('shown, then the server says no: nothing the earlier read held stays on screen', async () => {
    mockGetPv.mockResolvedValueOnce(builderView());
    const tree = await render(<PotentialVariationDetail />);
    let text = allText(tree);
    expect(text).toContain(TITLE);
    expect(text).toContain(NOTE);
    expect(text).toContain(SECOND_LINE);

    // E.g. this admin was made a contributor while the screen stayed open:
    // from now on the server refuses every read.
    mockGetPv.mockRejectedValue(httpError(404, 'Potential variation not found'));
    await act(async () => {
      await client.refetchQueries({ queryKey: DETAIL_KEY, exact: true });
    });
    await flush();

    // The screen shows none of it, and the cache no longer holds it.
    expect(byTestId(tree, 'pv-not-found')).toHaveLength(1);
    text = allText(tree);
    for (const secret of [TITLE, NOTE, SECOND_LINE]) expect(text).not.toContain(secret);
    expect(byTestId(tree, 'pv-decision-save')).toHaveLength(0);
    expect(client.getQueryData(DETAIL_KEY)).toBeUndefined();

    // A later read that merely fails (no signal) replaces the 404 - and
    // still brings nothing back: there is nothing left to show.
    mockGetPv.mockRejectedValueOnce(noSignal());
    await act(async () => {
      await client.refetchQueries({ queryKey: DETAIL_KEY, exact: true });
    });
    await flush();
    text = allText(tree);
    for (const secret of [TITLE, NOTE, SECOND_LINE]) expect(text).not.toContain(secret);
    expect(byTestId(tree, 'pv-load-failed')).toHaveLength(1);
  });

  it('a refetch that merely fails (no signal) keeps showing the flag, and says it may be old', async () => {
    mockGetPv.mockResolvedValueOnce(builderView());
    const tree = await render(<PotentialVariationDetail />);
    mockGetPv.mockRejectedValueOnce(noSignal());
    await act(async () => {
      await client.refetchQueries({ queryKey: DETAIL_KEY, exact: true });
    });
    await flush();
    expect(allText(tree)).toContain(TITLE);
    expect(byTestId(tree, 'pv-not-refreshed')).toHaveLength(1);
  });

  it('a first read that fails offers a read again, which recovers', async () => {
    mockGetPv.mockRejectedValueOnce(noSignal());
    const tree = await render(<PotentialVariationDetail />);
    expect(byTestId(tree, 'pv-load-failed')).toHaveLength(1);
    expect(byTestId(tree, 'pv-not-found')).toHaveLength(0);

    mockGetPv.mockResolvedValueOnce(builderView());
    press(tree, 'pv-load-retry');
    await flush();
    expect(byTestId(tree, 'pv-load-failed')).toHaveLength(0);
    expect(allText(tree)).toContain(NOTE);
  });
});

describe('the flag screen', () => {
  beforeEach(() => {
    mockParams = { eventId: 'event-1' };
    // The entry was read earlier (its own screen), so the cache has it.
    client.setQueryData(ENTRY_KEY, ENTRY);
  });

  it('an entry read before and refused now: its text is not shown and nothing can be flagged', async () => {
    mockGetEvent.mockRejectedValue(httpError(404, 'Site log event not found'));
    const tree = await render(<NewPotentialVariationScreen />);
    expect(byTestId(tree, 'pv-entry-gone')).toHaveLength(1);
    expect(allText(tree)).not.toContain(SECOND_LINE);
    expect(byTestId(tree, 'pv-save')).toHaveLength(0);
  });

  it('an entry that cannot be read right now (no signal): no form, and a way to read again', async () => {
    mockGetEvent.mockRejectedValueOnce(noSignal());
    const tree = await render(<NewPotentialVariationScreen />);
    expect(byTestId(tree, 'pv-entry-load-failed')).toHaveLength(1);
    expect(byTestId(tree, 'pv-save')).toHaveLength(0);

    mockGetEvent.mockResolvedValueOnce(ENTRY);
    press(tree, 'pv-entry-retry');
    await flush();
    expect(byTestId(tree, 'pv-save').length).toBeGreaterThan(0);
  });

  it('a flag in doubt on an entry refused now: asked about by its key, its saved title not shown', async () => {
    await act(async () => {
      await usePvPendingCreates.getState().putDurable({
        create_client_id: 'lost-key',
        user_id: 'me-1',
        site_log_event_id: 'event-1',
        source_revision_no: 1,
        title: TITLE,
        created_at: 1,
      });
    });
    mockGetEvent.mockRejectedValue(httpError(404, 'Site log event not found'));
    mockFind.mockResolvedValue(null);
    const tree = await render(<NewPotentialVariationScreen />);
    expect(mockFind).toHaveBeenCalledWith('lost-key');
    expect(byTestId(tree, 'pv-pending-not-found')).toHaveLength(1);
    expect(allText(tree)).not.toContain(TITLE);
    expect(allText(tree)).toContain('potentialVariations.placeholder_title');
  });
});

describe("the entry's section", () => {
  it('rows shown, then the server says no: they go, and a later failed read brings none back', async () => {
    const row = { ...builderView(), source: { site_log_event_id: 'event-1', revision_no: 1, readable: true, job_id: null } };
    mockListEvent.mockResolvedValueOnce([row]);
    const tree = await render(<EntryPvSection eventId="event-1" withdrawn={false} />);
    expect(allText(tree)).toContain(TITLE);

    mockListEvent.mockRejectedValue(httpError(404, 'Site log event not found'));
    await act(async () => {
      await client.refetchQueries({ queryKey: ['potential-variations', 'event', 'event-1'] });
    });
    await flush();
    expect(byTestId(tree, 'entry-pv-unavailable')).toHaveLength(1);
    expect(allText(tree)).not.toContain(TITLE);

    mockListEvent.mockRejectedValueOnce(noSignal());
    await act(async () => {
      await client.refetchQueries({ queryKey: ['potential-variations', 'event', 'event-1'] });
    });
    await flush();
    expect(allText(tree)).not.toContain(TITLE);
  });
});
