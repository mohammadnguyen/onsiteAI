/**
 * The saved-record screen: what it shows in the app, and what it refuses
 * to show once the session or the screen it was opened on has gone.
 *
 * Renders the real screen with react-test-renderer. The download is the
 * in-memory file system's, planned per call, so the test can hold one open
 * while it signs the user out or moves them off the screen.
 *
 * Lives under src/ for the same reason screenTiming.test.tsx does: a file
 * under app/ is a route.
 */
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';

// ---- focus, controlled by the test --------------------------------------
const mockFocusCleanups: (() => void)[] = [];
function blurEverything(): void {
  while (mockFocusCleanups.length > 0) mockFocusCleanups.pop()?.();
}

jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), push: jest.fn(), back: jest.fn(), canGoBack: () => true },
  useLocalSearchParams: () => ({ id: 'event-1' }),
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const react = require('react') as typeof import('react');
    react.useEffect(() => {
      const cleanup = cb();
      if (cleanup) mockFocusCleanups.push(cleanup);
      return () => {
        if (cleanup) {
          const at = mockFocusCleanups.indexOf(cleanup);
          if (at >= 0) mockFocusCleanups.splice(at, 1);
          cleanup();
        }
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
  },
}));

jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

// The insets a provider reports on a phone with a Dynamic Island. The
// viewer must pad by these - from the provider it renders INSIDE the
// Modal - not by a SafeAreaView of its own. That provider is a pass-through
// here (its native measurement never happens in jest); the test asserts it
// is in place, the library's own source says what it does.
let mockInsets = { top: 59, bottom: 34, left: 0, right: 0 };
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context');
  const MockProvider = ({ children }: { children: unknown }) => children;
  return { ...actual, useSafeAreaInsets: () => mockInsets, SafeAreaProvider: MockProvider };
});

// The record, read lazily so each test can shape it.
let mockEvent: Record<string, unknown> | undefined;
jest.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ isLoading: false, data: mockEvent }),
}));

const mockApiGet = jest.fn();
jest.mock('../../api/client', () => ({
  api: { defaults: { baseURL: 'https://api.example' }, get: (...a: unknown[]) => mockApiGet(...a) },
}));

jest.mock('../BackLink', () => ({ BackLink: () => null }));
jest.mock('../../ui/kit', () => ({ StatusBadge: () => null }));
jest.mock('expo-file-system/legacy', () => require('./support/memfs'));

const mockShare = jest.fn();
let mockShareHold: Promise<void> | null = null;
const mockShareAvailable = jest.fn(async () => {
  if (mockShareHold) await mockShareHold;
  return true;
});
jest.mock('expo-sharing', () => ({
  isAvailableAsync: () => mockShareAvailable(),
  shareAsync: (...a: unknown[]) => mockShare(...a),
}));

// One player instance, and a status the test sets.
const mockPlayer = {
  replace: jest.fn(),
  play: jest.fn(),
  pause: jest.fn(),
  seekTo: jest.fn(async () => undefined),
};
let mockStatus = {
  isLoaded: false,
  playing: false,
  currentTime: 0,
  duration: 0,
  didJustFinish: false,
  isBuffering: false,
  playbackState: 'unknown',
};
// Holdable, so a test can change the session while the mode call is pending.
let mockAudioModeHold: Promise<void> | null = null;
const mockSetAudioMode = jest.fn(async (..._a: unknown[]) => {
  if (mockAudioModeHold) await mockAudioModeHold;
});
jest.mock('expo-audio', () => ({
  useAudioPlayer: () => mockPlayer,
  useAudioPlayerStatus: () => mockStatus,
  setAudioModeAsync: (...a: unknown[]) => mockSetAudioMode(...a),
}));

// The web view, as a host element that keeps its props for inspection.
jest.mock('react-native-webview', () => ({
  WebView: (props: Record<string, unknown>) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const react = require('react') as typeof import('react');
    return react.createElement('WebView', props);
  },
}));

import { memfs } from './support/memfs';
import { useAuthStore } from '../../store/auth';
import SiteLogRecordDetail from '../../../app/site-log/[id]';

type Att = {
  attachment_client_id: string;
  declared_media_type: 'text' | 'audio' | 'image' | 'document';
  declared_size_bytes: number | null;
  state: 'awaiting_upload' | 'pending' | 'stored' | 'failed';
  evidence_id: string | null;
  is_inline_text?: boolean;
};

function att(
  id: string,
  type: Att['declared_media_type'],
  extra: Partial<Att> = {},
): Att {
  return {
    attachment_client_id: id,
    declared_media_type: type,
    declared_size_bytes: 1,
    state: 'stored',
    evidence_id: `ev-${id}`,
    ...extra,
  };
}

function eventWith(attachments: Att[], body: string | null = 'Body text'): Record<string, unknown> {
  return {
    site_log_event_id: 'event-1',
    capture_client_id: 'cap-1',
    author_user_id: 'user-a',
    job_id: null,
    job_state: 'unassigned',
    capture_status: 'complete',
    created_at: '2026-09-20T10:00:00Z',
    revision: {
      revision_no: 1,
      body_text: body,
      internal_location: null,
      occurred_at: null,
      withdrawn: false,
      created_at: '2026-09-20T10:00:00Z',
    },
    attachments,
  };
}

async function flush(): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

/** Press a control the way a finger would get to: it must be a Pressable,
 *  enabled, with no ancestor refusing pointer events. Calling onPress on a
 *  bare View would "pass" here and do nothing on a phone. */
function press(tree: ReactTestRenderer, testID: string): void {
  const node = tree.root.findByProps({ testID });
  // Pressable is exported wrapped in React.memo; the renderer reports the
  // inner component as the instance type.
  const P = require('react-native').Pressable as { type?: unknown };
  expect([P, P.type]).toContain(node.type);
  expect(node.props.disabled).toBeFalsy();
  for (const ancestor of ancestorsOf(tree, node)) {
    expect(ancestor.props.pointerEvents).not.toBe('none');
  }
  (node.props as { onPress: () => void }).onPress();
}

/** Every instance from the root down to (excluding) `node`. */
function ancestorsOf(tree: ReactTestRenderer, node: ReactTestInstance): ReactTestInstance[] {
  const path: ReactTestInstance[] = [];
  const walk = (n: ReactTestInstance, trail: ReactTestInstance[]): boolean => {
    if (n === node) {
      path.push(...trail);
      return true;
    }
    return n.children.some((c) => typeof c !== 'string' && walk(c, [...trail, n]));
  };
  walk(tree.root, []);
  return path;
}

function has(tree: ReactTestRenderer, testID: string): boolean {
  return tree.root.findAllByProps({ testID }).length > 0;
}

/** Host nodes only - a component and the host it renders both carry the
 *  testID, so an unfiltered count is double. */
function countHost(tree: ReactTestRenderer, testID: string): number {
  return tree.root.findAll((n) => typeof n.type === 'string' && n.props.testID === testID)
    .length;
}

function showsText(tree: ReactTestRenderer, text: string): boolean {
  return tree.root.findAll((n) => n.props.children === text).length > 0;
}

function textOf(tree: ReactTestRenderer, testID: string): string {
  const node = tree.root.findByProps({ testID });
  const children = node.props.children;
  return Array.isArray(children) ? children.join('') : String(children);
}

function render(): ReactTestRenderer {
  let tree!: ReactTestRenderer;
  act(() => {
    tree = create(<SiteLogRecordDetail />);
  });
  return tree;
}

/** Files carry the visit id (time + random, base 36), so they are matched by
 *  shape, not spelled out. */
function fileFor(evidence: string, ext: string): RegExp {
  return new RegExp(`^file:///cache/sitelog-v[a-z0-9]+-[a-z0-9]+-${evidence}\\.${ext}$`);
}

function cachedFiles(): string[] {
  return [...memfs.files.keys()].filter((k) => k.startsWith('file:///cache/'));
}

beforeEach(() => {
  memfs.reset();
  mockApiGet.mockReset();
  mockShare.mockReset();
  mockShareAvailable.mockClear();
  mockSetAudioMode.mockClear();
  mockPlayer.replace.mockClear();
  mockPlayer.play.mockClear();
  mockPlayer.pause.mockClear();
  mockStatus = {
    isLoaded: false,
    playing: false,
    currentTime: 0,
    duration: 0,
    didJustFinish: false,
    isBuffering: false,
    playbackState: 'unknown',
  };
  mockAudioModeHold = null;
  mockShareHold = null;
  mockInsets = { top: 59, bottom: 34, left: 0, right: 0 };
  mockFocusCleanups.length = 0;
  act(() => {
    useAuthStore.setState({ accessToken: 'tok-a', userId: 'user-a', sessionNonce: 1 });
  });
});

describe('what is listed', () => {
  it('shows the body once: the server-marked inline copy is not a file', () => {
    mockEvent = eventWith([
      att('inline', 'text', { is_inline_text: true }),
      att('img', 'image'),
    ]);
    const tree = render();
    expect(textOf(tree, 'detail-body')).toBe('Body text');
    expect(countHost(tree, 'attachment-row')).toBe(1);
    expect(has(tree, 'view:img')).toBe(true);
    expect(has(tree, 'share:inline')).toBe(false);
  });

  it('keeps a text file the user attached: only an explicit flag hides a row', () => {
    mockEvent = eventWith([
      att('notes', 'text'), // older backend: no flag at all
      att('csv', 'text', { is_inline_text: false }),
    ]);
    const tree = render();
    expect(countHost(tree, 'attachment-row')).toBe(2);
    expect(has(tree, 'share:notes')).toBe(true);
    expect(has(tree, 'share:csv')).toBe(true);
  });

  it('offers nothing to open for an attachment that is not stored', () => {
    mockEvent = eventWith([att('img', 'image', { state: 'failed', evidence_id: null })]);
    const tree = render();
    expect(has(tree, 'view:img')).toBe(false);
    expect(has(tree, 'share:img')).toBe(false);
  });
});

describe('photo', () => {
  it('is shown here, with a close control, from the downloaded file', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 200, headers: { 'Content-Type': 'image/jpeg' } });
    const tree = render();
    expect(has(tree, 'viewer-image')).toBe(false);

    await act(async () => press(tree, 'view:img'));
    await flush();

    expect(memfs.downloadCalls).toEqual([
      expect.objectContaining({
        url: 'https://api.example/evidence/ev-img/download',
        headers: { Authorization: 'Bearer tok-a' },
      }),
    ]);
    expect(has(tree, 'viewer-image')).toBe(true);
    const image = tree.root.findByProps({ testID: 'viewer-image-body' });
    expect(image.props.source.uri).toMatch(fileFor('ev-img', 'jpg'));
    // The scratch file is gone; only the promoted file remains.
    expect(cachedFiles()).toEqual([image.props.source.uri]);

    await act(async () => press(tree, 'viewer-close'));
    expect(has(tree, 'viewer-image')).toBe(false);

    // Leaving the screen removes what this visit downloaded.
    act(() => tree.unmount());
    await flush();
    expect(cachedFiles()).toEqual([]);
  });

  it('keeps its controls inside the safe area, from the root provider, and closes from either end', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'image/jpeg' } });
    const tree = render();
    await act(async () => press(tree, 'view:img'));
    await flush();

    // Padded by the insets of the provider rendered inside the Modal - not
    // a SafeAreaView, which inside a Modal reads nothing.
    const RN = require('react-native');
    const SAC = require('react-native-safe-area-context');
    const modal = tree.root.findByType(RN.Modal);
    // The Modal's first child is its own provider; nothing pads between
    // the Modal and viewer-root; no SafeAreaView anywhere inside the Modal.
    expect(modal.findAllByType(SAC.SafeAreaView)).toHaveLength(0);
    expect(modal.findAllByType(SAC.SafeAreaProvider)).toHaveLength(1);
    // Host nodes in document order: the Modal's own host element, then -
    // with nothing padding in between - viewer-root.
    const hosts = modal.findAll((n) => typeof n.type === 'string');
    const firstHost = hosts[1];
    expect(firstHost.props.testID).toBe('viewer-root');
    const root = tree.root.findByProps({ testID: 'viewer-root' });
    const style = Object.assign({}, ...[root.props.style].flat(Infinity).filter(Boolean));
    expect(style.paddingTop).toBe(59);
    expect(style.paddingBottom).toBe(34);
    expect(root.props.pointerEvents).not.toBe('none');
    // The bottom Close is viewer-root's LAST child, outside the zoomable
    // body, and nothing else in the frame is absolutely positioned over it.
    const kids = firstHost.children.filter((c): c is ReactTestInstance => typeof c !== 'string');
    expect(kids[kids.length - 1].props.testID).toBe('viewer-close-bottom');
    expect(tree.root.findByProps({ testID: 'viewer-image' }).findAllByProps({ testID: 'viewer-close-bottom' })).toHaveLength(0);
    const absolute = kids
      .filter((k) => Object.assign({}, ...[k.props.style].flat(Infinity).filter(Boolean)).position === 'absolute')
      .map((k) => k.props.testID);
    expect(absolute).toEqual([]);

    // Insets follow the provider: an in-call status bar on an older phone
    // grows the top inset, and the bar moves with it.
    mockInsets = { top: 79, bottom: 34, left: 0, right: 0 };
    await act(async () => {
      tree.update(<SiteLogRecordDetail />);
    });
    const restyled = Object.assign(
      {},
      ...[tree.root.findByProps({ testID: 'viewer-root' }).props.style].flat(Infinity).filter(Boolean),
    );
    expect(restyled.paddingTop).toBe(79);

    // Bottom Close closes.
    expect(has(tree, 'viewer-close-bottom')).toBe(true);
    await act(async () => press(tree, 'viewer-close-bottom'));
    expect(has(tree, 'viewer-image')).toBe(false);
    expect(has(tree, 'viewer-root')).toBe(false);

    // Top Close closes too.
    await act(async () => press(tree, 'view:img'));
    await flush();
    expect(has(tree, 'viewer-image')).toBe(true);
    await act(async () => press(tree, 'viewer-close'));
    expect(has(tree, 'viewer-image')).toBe(false);
  });

  it('can be left while a photo has failed to display', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'image/jpeg' } });
    const tree = render();
    await act(async () => press(tree, 'view:img'));
    await flush();
    const image = tree.root.findByProps({ testID: 'viewer-image-body' });
    await act(async () => (image.props as { onError: () => void }).onError());
    expect(has(tree, 'viewer-failed')).toBe(true);
    expect(has(tree, 'viewer-close')).toBe(true);
    await act(async () => press(tree, 'viewer-close-bottom'));
    expect(has(tree, 'viewer-root')).toBe(false);
    expect(has(tree, 'viewer-failed')).toBe(false);
  });

  it('is never deleted by a download that a PREVIOUS visit started and finished late', async () => {
    // Visit 1 starts a download and the user leaves before it finishes.
    // Visit 2 (same record) downloads and shows the same attachment. Then
    // visit 1's download completes: it must not touch visit 2's file.
    mockEvent = eventWith([att('img', 'image')]);
    let releaseOld!: () => void;
    const holdOld = new Promise<void>((r) => {
      releaseOld = r;
    });
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'image/jpeg' }, hold: holdOld },
      { status: 200, headers: { 'content-type': 'image/jpeg' } },
    );
    const visit1 = render();
    await act(async () => press(visit1, 'view:img'));
    act(() => visit1.unmount());

    const visit2 = render();
    await act(async () => press(visit2, 'view:img'));
    await flush();
    const shown = visit2.root.findByProps({ testID: 'viewer-image-body' }).props.source.uri as string;
    expect(shown).toMatch(fileFor('ev-img', 'jpg'));
    expect(memfs.files.has(shown)).toBe(true);

    releaseOld();
    await flush();

    // Visit 2's file is intact and still the only cached file; visit 1's
    // late bytes left nothing behind.
    expect(memfs.files.has(shown)).toBe(true);
    expect(cachedFiles()).toEqual([shown]);
    expect(has(visit2, 'viewer-image')).toBe(true);
  });
});

describe('PDF', () => {
  it('is read here, in a web view pointed at the local file, never at a service', async () => {
    mockEvent = eventWith([att('doc', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    const tree = render();

    await act(async () => press(tree, 'view:doc'));
    await flush();

    const web = tree.root.findByProps({ testID: 'viewer-pdf' });
    expect(web.props.source.uri).toMatch(fileFor('ev-doc', 'pdf'));
    // Read access is the document itself, not the cache directory.
    expect(web.props.allowingReadAccessToURL).toBe(web.props.source.uri);
    expect(has(tree, 'viewer-failed')).toBe(false);

    // Still loading (no onLoadEnd yet): both Close controls are there, the
    // bottom one is not inside the web view, the spinner is the only
    // absolutely positioned element, and the bottom one leaves.
    expect(has(tree, 'viewer-spinner')).toBe(true);
    expect(has(tree, 'viewer-close')).toBe(true);
    expect(web.findAllByProps({ testID: 'viewer-close-bottom' })).toHaveLength(0);
    const frame = tree.root.findAll((n) => typeof n.type === 'string' && n.props.testID === 'viewer-root')[0];
    const absolute = frame.children
      .filter((c): c is ReactTestInstance => typeof c !== 'string')
      .filter((k) => Object.assign({}, ...[k.props.style].flat(Infinity).filter(Boolean)).position === 'absolute')
      .map((k) => k.props.testID);
    expect(absolute).toEqual(['viewer-spinner']);
    await act(async () => press(tree, 'viewer-close-bottom'));
    expect(has(tree, 'viewer-root')).toBe(false);

    // The platform's own request to close (Android back) leaves too, in
    // the state the device got stuck in.
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    await act(async () => press(tree, 'view:doc'));
    await flush();
    expect(has(tree, 'viewer-spinner')).toBe(true);
    const modal = tree.root.findByType(require('react-native').Modal);
    await act(async () => (modal.props as { onRequestClose: () => void }).onRequestClose());
    expect(has(tree, 'viewer-root')).toBe(false);

    // Failed: both Close controls are there and the top one leaves.
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    await act(async () => press(tree, 'view:doc'));
    await flush();
    const web2 = tree.root.findByProps({ testID: 'viewer-pdf' });
    await act(async () => (web2.props as { onError: () => void }).onError());
    expect(has(tree, 'viewer-failed')).toBe(true);
    expect(has(tree, 'viewer-close-bottom')).toBe(true);
    await act(async () => press(tree, 'viewer-close'));
    expect(has(tree, 'viewer-root')).toBe(false);
    expect(has(tree, 'viewer-failed')).toBe(false);
  });

  it('lets only the opened document load: a link inside the PDF goes nowhere', async () => {
    mockEvent = eventWith([att('doc', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    const tree = render();
    await act(async () => press(tree, 'view:doc'));
    await flush();

    const web = tree.root.findByProps({ testID: 'viewer-pdf' });
    // '*' on purpose: anything outside the whitelist is handed to the OS
    // browser by the library WITHOUT asking the callback. Everything must
    // reach the callback, which then refuses.
    expect(web.props.originWhitelist).toEqual(['*']);
    // Link previews off: a long-press preview fetches the remote page and
    // opens Safari on commit, and none of that is a navigation the callback
    // sees.
    expect(web.props.allowsLinkPreview).toBe(false);
    const may = web.props.onShouldStartLoadWithRequest as (r: { url: string }) => boolean;
    const uri = web.props.source.uri as string;
    const name = uri.slice(uri.lastIndexOf('/') + 1);
    expect(may({ url: uri })).toBe(true);
    // The same file, percent-encoded or with a fragment, is the same file.
    expect(may({ url: uri.replace('.pdf', '%2Epdf') })).toBe(true);
    expect(may({ url: `${uri}#page=2` })).toBe(true);
    // Everything else is refused - including a same-named file in another
    // directory, which is a different file.
    expect(may({ url: 'https://example.com/drawing.pdf' })).toBe(false);
    expect(may({ url: 'http://converter.example/upload' })).toBe(false);
    expect(may({ url: uri.replace('ev-doc', 'ev-other') })).toBe(false);
    expect(may({ url: `file:///cache/other/${name}` })).toBe(false);
    expect(may({ url: uri.replace('file:///cache/', 'file:///private/cache/') })).toBe(false);
    expect(may({ url: uri.replace('file:///cache/', 'file:///cache/../cache/') })).toBe(false);
    expect(may({ url: 'file:///documents/site-log/user-a/x/recording.m4a' })).toBe(false);
    // The full policy, including the iOS /private/var alias, is tested on
    // its own in localDocument.test.ts.
  });

  it('fences the document that is open NOW, not the one opened before', async () => {
    mockEvent = eventWith([att('a', 'document'), att('b', 'document')]);
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'application/pdf' } },
      { status: 200, headers: { 'content-type': 'application/pdf' } },
    );
    const tree = render();
    await act(async () => press(tree, 'view:a'));
    await flush();
    let web = tree.root.findByProps({ testID: 'viewer-pdf' });
    let may = web.props.onShouldStartLoadWithRequest as (r: { url: string }) => boolean;
    const uriA = web.props.source.uri as string;
    expect(uriA).toMatch(fileFor('ev-a', 'pdf'));
    expect(may({ url: uriA })).toBe(true);
    expect(may({ url: uriA.replace('ev-a', 'ev-b') })).toBe(false);
    await act(async () => press(tree, 'viewer-close'));

    await act(async () => press(tree, 'view:b'));
    await flush();
    web = tree.root.findByProps({ testID: 'viewer-pdf' });
    may = web.props.onShouldStartLoadWithRequest as (r: { url: string }) => boolean;
    const uriB = web.props.source.uri as string;
    expect(uriB).toMatch(fileFor('ev-b', 'pdf'));
    expect(web.props.allowingReadAccessToURL).toBe(uriB);
    expect(may({ url: uriB })).toBe(true);
    expect(may({ url: uriA })).toBe(false);
  });

  it('is not offered as an in-app view on Android, where the web view cannot render it', async () => {
    const { Platform } = require('react-native');
    const was = Platform.OS;
    Platform.OS = 'android';
    try {
      mockEvent = eventWith([att('doc', 'document')]);
      memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
      const tree = render();
      await act(async () => press(tree, 'view:doc'));
      await flush();

      expect(has(tree, 'viewer-pdf')).toBe(false);
      expect(showsText(tree, 'siteLog.detail.no_viewer')).toBe(true);
      expect(has(tree, 'share:doc')).toBe(true);
    } finally {
      Platform.OS = was;
    }
  });

  it('does not leave its spinner over the next photo when closed before it loaded', async () => {
    mockEvent = eventWith([att('doc', 'document'), att('img', 'image')]);
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'application/pdf' } },
      { status: 200, headers: { 'content-type': 'image/jpeg' } },
    );
    const tree = render();
    await act(async () => press(tree, 'view:doc'));
    await flush();
    expect(has(tree, 'viewer-spinner')).toBe(true);
    // Closed before onLoadEnd ever fired.
    await act(async () => press(tree, 'viewer-close'));

    await act(async () => press(tree, 'view:img'));
    await flush();
    expect(has(tree, 'viewer-image')).toBe(true);
    expect(has(tree, 'viewer-spinner')).toBe(false);
  });

  it('says so, and leaves Share, for a document it cannot render', async () => {
    mockEvent = eventWith([att('dwg', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/acad' } });
    const tree = render();

    await act(async () => press(tree, 'view:dwg'));
    await flush();

    expect(has(tree, 'viewer-pdf')).toBe(false);
    expect(has(tree, 'viewer-image')).toBe(false);
    expect(showsText(tree, 'siteLog.detail.no_viewer')).toBe(true);
    expect(has(tree, 'share:dwg')).toBe(true);
  });
});

describe('recording', () => {
  it('sets playback mode - audible on a silenced phone - before it plays', async () => {
    mockEvent = eventWith([att('voice', 'audio')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
    const tree = render();

    await act(async () => press(tree, 'audio-toggle:voice'));
    await flush();

    expect(mockSetAudioMode).toHaveBeenCalledWith(
      expect.objectContaining({ playsInSilentMode: true, allowsRecording: false }),
    );
    expect(mockPlayer.replace).toHaveBeenCalledWith({
      uri: expect.stringMatching(fileFor('ev-voice', 'm4a')),
    });
    expect(mockPlayer.play).toHaveBeenCalledTimes(1);
    // Mode first, then the source, then play.
    const order = [
      mockSetAudioMode.mock.invocationCallOrder[0],
      mockPlayer.replace.mock.invocationCallOrder[0],
      mockPlayer.play.mock.invocationCallOrder[0],
    ];
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(has(tree, 'audio-player')).toBe(true);
  });

  it('shows elapsed and total time, and pauses on the second tap', async () => {
    mockEvent = eventWith([att('voice', 'audio')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
    const tree = render();
    await act(async () => press(tree, 'audio-toggle:voice'));
    await flush();

    mockStatus = { ...mockStatus, isLoaded: true, playing: true, currentTime: 5, duration: 65 };
    await act(async () => {
      tree.update(<SiteLogRecordDetail />);
    });
    expect(showsText(tree, '0:05 / 1:05')).toBe(true);

    await act(async () => press(tree, 'audio-toggle:voice'));
    expect(mockPlayer.pause).toHaveBeenCalledTimes(1);
    expect(mockPlayer.replace).toHaveBeenCalledTimes(1); // not reloaded
  });

  it('reports a recording the native player rejects after it was handed over', async () => {
    mockEvent = eventWith([att('voice', 'audio')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
    const tree = render();
    await act(async () => press(tree, 'audio-toggle:voice'));
    await flush();
    expect(mockPlayer.play).toHaveBeenCalledTimes(1);

    // Corrupt file: decoding fails after replace/play returned.
    mockStatus = { ...mockStatus, playbackState: 'failed' };
    await act(async () => {
      tree.update(<SiteLogRecordDetail />);
    });
    expect(has(tree, 'audio-failed')).toBe(true);
    expect(textOf(tree, 'audio-failed')).toBe('siteLog.detail.audio_failed');
    expect(has(tree, 'audio-player')).toBe(false);
    expect(mockPlayer.pause).toHaveBeenCalled();
    expect(has(tree, 'share:voice')).toBe(true);
  });

  it('does not read the previous recording\'s failure as the next one\'s', async () => {
    // The status hook keeps the last native status until a new event
    // arrives. After recording A fails, tapping recording B must not see
    // A's "failed" and stop B.
    mockEvent = eventWith([att('a', 'audio'), att('b', 'audio')]);
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'audio/m4a' } },
      { status: 200, headers: { 'content-type': 'audio/m4a' } },
    );
    const tree = render();
    await act(async () => press(tree, 'audio-toggle:a'));
    await flush();
    mockStatus = { ...mockStatus, playbackState: 'failed' };
    await act(async () => {
      tree.update(<SiteLogRecordDetail />);
    });
    expect(has(tree, 'audio-failed')).toBe(true);
    const pausesAfterA = mockPlayer.pause.mock.calls.length;

    // No new native event yet: the status is still A's failure.
    await act(async () => press(tree, 'audio-toggle:b'));
    await flush();

    expect(mockPlayer.replace).toHaveBeenLastCalledWith({
      uri: expect.stringMatching(fileFor('ev-b', 'm4a')),
    });
    expect(mockPlayer.play).toHaveBeenCalledTimes(2);
    expect(mockPlayer.pause.mock.calls.length).toBe(pausesAfterA);
    expect(has(tree, 'audio-player')).toBe(true);
    expect(has(tree, 'audio-failed')).toBe(false);

    // B then genuinely fails: a NEW status says so, and that one counts.
    mockStatus = { ...mockStatus, playbackState: 'failed' };
    await act(async () => {
      tree.update(<SiteLogRecordDetail />);
    });
    expect(has(tree, 'audio-failed')).toBe(true);
    expect(has(tree, 'audio-player')).toBe(false);
  });

  it('does not read Android\'s "loaded but idle" as ready: the clock still runs, and it fails', async () => {
    // expo-audio on Android derives isLoaded from "no longer loading" -
    // which is also what a decode failure looks like, with the player idle.
    jest.useFakeTimers();
    try {
      mockEvent = eventWith([att('voice', 'audio')]);
      memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
      const tree = render();
      await act(async () => press(tree, 'audio-toggle:voice'));
      await flush();

      mockStatus = { ...mockStatus, isLoaded: true, playbackState: 'idle' };
      await act(async () => {
        tree.update(<SiteLogRecordDetail />);
      });
      // Not shown as playable; a second tap does not take the "already
      // loaded, just toggle" path - it prepares the player again.
      expect(showsText(tree, 'siteLog.detail.audio_loading')).toBe(true);
      await act(async () => press(tree, 'audio-toggle:voice'));
      await flush();
      expect(mockPlayer.replace).toHaveBeenCalledTimes(2);
      expect(mockPlayer.play).toHaveBeenCalledTimes(2);
      expect(mockPlayer.pause).not.toHaveBeenCalled();

      await act(async () => {
        jest.advanceTimersByTime(21_000);
      });
      expect(has(tree, 'audio-failed')).toBe(true);
      expect(has(tree, 'audio-player')).toBe(false);
      expect(has(tree, 'share:voice')).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  it('gives up on a recording that never loads, instead of "Loading…" for ever', async () => {
    jest.useFakeTimers();
    try {
      mockEvent = eventWith([att('voice', 'audio')]);
      memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
      const tree = render();
      await act(async () => press(tree, 'audio-toggle:voice'));
      await flush();
      expect(has(tree, 'audio-player')).toBe(true);
      expect(has(tree, 'audio-failed')).toBe(false);

      await act(async () => {
        jest.advanceTimersByTime(19_000);
      });
      expect(has(tree, 'audio-failed')).toBe(false);
      await act(async () => {
        jest.advanceTimersByTime(2_000);
      });
      expect(has(tree, 'audio-failed')).toBe(true);
      expect(has(tree, 'audio-player')).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });

  it('says so when the recording cannot be fetched', async () => {
    mockEvent = eventWith([att('voice', 'audio')]);
    memfs.downloads.push({ status: 200, error: new Error('connection dropped') });
    const tree = render();
    await act(async () => press(tree, 'audio-toggle:voice'));
    await flush();

    expect(mockPlayer.replace).not.toHaveBeenCalled();
    expect(has(tree, 'audio-failed')).toBe(true);
    expect(textOf(tree, 'audio-failed')).toBe('siteLog.error.download');
  });
});

describe('the download', () => {
  it('refreshes once on 401 and retries with the new token', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 401 }, { status: 200, headers: { 'content-type': 'image/png' } });
    mockApiGet.mockImplementation(async () => {
      useAuthStore.setState({ accessToken: 'tok-b' });
      return { data: {} };
    });
    const tree = render();

    await act(async () => press(tree, 'view:img'));
    await flush();

    expect(mockApiGet).toHaveBeenCalledWith('/auth/me');
    expect(memfs.downloadCalls.map((c) => c.headers?.Authorization)).toEqual([
      'Bearer tok-a',
      'Bearer tok-b',
    ]);
    expect(has(tree, 'viewer-image')).toBe(true);
  });

  it('keeps a row busy until its OWN download ends, whatever else is tapped', async () => {
    // View A, then View B before A has finished. Row A must stay disabled
    // (so a second tap cannot start a second download onto the same files)
    // while B opens and closes; when A's bytes arrive, one download, one
    // promotion, no spurious error.
    mockEvent = eventWith([att('a', 'document'), att('b', 'image')]);
    let releaseA!: () => void;
    const holdA = new Promise<void>((r) => {
      releaseA = r;
    });
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'application/pdf' }, hold: holdA },
      { status: 200, headers: { 'content-type': 'image/jpeg' } },
    );
    const tree = render();

    await act(async () => press(tree, 'view:a'));
    await act(async () => press(tree, 'view:b'));
    await flush();
    // B opened; A is still downloading and still shows as busy - both of
    // its controls refuse a tap.
    expect(has(tree, 'viewer-image')).toBe(true);
    await act(async () => press(tree, 'viewer-close'));
    expect(tree.root.findByProps({ testID: 'view:a' }).props.disabled).toBe(true);
    expect(tree.root.findByProps({ testID: 'share:a' }).props.disabled).toBe(true);
    expect(tree.root.findByProps({ testID: 'view:b' }).props.disabled).toBeFalsy();

    releaseA();
    await flush();

    expect(memfs.downloadCalls.filter((c) => c.url.endsWith('/ev-a/download'))).toHaveLength(1);
    expect(has(tree, 'viewer-pdf')).toBe(true);
    expect(showsText(tree, 'siteLog.error.download')).toBe(false);
    expect(cachedFiles().filter((k) => fileFor('ev-a', 'pdf').test(k))).toHaveLength(1);
    expect(cachedFiles().filter((k) => k.endsWith('.part'))).toEqual([]);
  });

  it('caches nothing from a non-200 answer', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 500 });
    const tree = render();

    await act(async () => press(tree, 'view:img'));
    await flush();

    expect(has(tree, 'viewer-image')).toBe(false);
    expect(cachedFiles()).toEqual([]);
    expect(showsText(tree, 'siteLog.error.download')).toBe(true);
  });
});

describe('a download that outlives its session or its screen', () => {
  it('opens nothing after the account signed out or changed', async () => {
    mockEvent = eventWith([att('img', 'image'), att('voice', 'audio')]);
    let release!: () => void;
    const hold = new Promise<void>((r) => {
      release = r;
    });
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'image/jpeg' }, hold });
    const tree = render();

    await act(async () => press(tree, 'view:img'));
    // Signed out while the bytes are still coming.
    act(() => useAuthStore.setState({ sessionNonce: 2, accessToken: 'tok-b' }));
    release();
    await flush();

    expect(has(tree, 'viewer-image')).toBe(false);

    // And the file that download produced is NOT what the next tap gets:
    // under the new session the attachment is fetched again, with the new
    // token.
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'image/jpeg' } });
    await act(async () => press(tree, 'view:img'));
    await flush();
    expect(memfs.downloadCalls.map((c) => c.headers?.Authorization)).toEqual([
      'Bearer tok-a',
      'Bearer tok-b',
    ]);
    expect(has(tree, 'viewer-image')).toBe(true);
    await act(async () => press(tree, 'viewer-close'));

    // Same for a recording: the player never receives the file.
    let release2!: () => void;
    const hold2 = new Promise<void>((r) => {
      release2 = r;
    });
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' }, hold: hold2 });
    await act(async () => press(tree, 'audio-toggle:voice'));
    act(() => useAuthStore.setState({ sessionNonce: 3, accessToken: 'tok-c' }));
    release2();
    await flush();
    expect(mockPlayer.replace).not.toHaveBeenCalled();
  });

  it('does not play when the session changed during the audio-mode call', async () => {
    // The download passed its check; the session changes while the mode
    // call - the NEXT await - is pending, and the screen stays focused.
    mockEvent = eventWith([att('voice', 'audio')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'audio/m4a' } });
    let release!: () => void;
    mockAudioModeHold = new Promise<void>((r) => {
      release = r;
    });
    const tree = render();

    await act(async () => press(tree, 'audio-toggle:voice'));
    await flush();
    expect(mockSetAudioMode).toHaveBeenCalled();
    act(() => useAuthStore.setState({ sessionNonce: 2 }));
    release();
    await flush();

    expect(mockPlayer.replace).not.toHaveBeenCalled();
    expect(mockPlayer.play).not.toHaveBeenCalled();
    expect(has(tree, 'audio-player')).toBe(false);
  });

  it('does not share when the session changed during the availability call', async () => {
    mockEvent = eventWith([att('doc', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    let release!: () => void;
    mockShareHold = new Promise<void>((r) => {
      release = r;
    });
    const tree = render();

    await act(async () => press(tree, 'share:doc'));
    await flush();
    expect(mockShareAvailable).toHaveBeenCalled();
    act(() => useAuthStore.setState({ sessionNonce: 2 }));
    release();
    await flush();

    expect(mockShare).not.toHaveBeenCalled();
  });

  it('opens nothing once the user has left the screen', async () => {
    mockEvent = eventWith([att('doc', 'document')]);
    let release!: () => void;
    const hold = new Promise<void>((r) => {
      release = r;
    });
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' }, hold });
    const tree = render();

    await act(async () => press(tree, 'view:doc'));
    blurEverything();
    release();
    await flush();

    expect(has(tree, 'viewer-pdf')).toBe(false);
  });

  it('closes the viewer and stops the player when the session changes', async () => {
    mockEvent = eventWith([att('img', 'image')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'image/jpeg' } });
    const tree = render();
    await act(async () => press(tree, 'view:img'));
    await flush();
    expect(has(tree, 'viewer-image')).toBe(true);

    await act(async () => {
      useAuthStore.setState({ sessionNonce: 2 });
    });
    expect(has(tree, 'viewer-image')).toBe(false);
    expect(mockPlayer.pause).toHaveBeenCalled();
  });
});

describe('share', () => {
  it('does not delete a file the share sheet is still holding when the screen goes', async () => {
    // An auth failure can redirect to login - unmounting this screen -
    // while the share sheet is open over it. The sheet, and on Android the
    // app the user picks, read the file AFTER that.
    mockEvent = eventWith([att('doc', 'document'), att('img', 'image')]);
    // Plans are consumed in press order: the photo is viewed first.
    memfs.downloads.push(
      { status: 200, headers: { 'content-type': 'image/jpeg' } },
      { status: 200, headers: { 'content-type': 'application/pdf' } },
    );
    let releaseShare!: () => void;
    mockShare.mockImplementation(() => new Promise<void>((r) => {
      releaseShare = r;
    }));
    const tree = render();
    // A viewed file, which the visit does own and should remove.
    await act(async () => press(tree, 'view:img'));
    await flush();
    await act(async () => press(tree, 'viewer-close'));
    // A shared file, held open by the sheet.
    await act(async () => press(tree, 'share:doc'));
    await flush();
    expect(mockShare).toHaveBeenCalledTimes(1);
    const sharedUri = mockShare.mock.calls[0][0] as string;
    expect(sharedUri).toMatch(fileFor('ev-doc', 'pdf'));

    act(() => tree.unmount());
    await flush();
    // The viewed file is gone; the shared one is still there for the sheet.
    expect(cachedFiles()).toEqual([sharedUri]);

    releaseShare();
    await flush();
    expect(memfs.files.has(sharedUri)).toBe(true);
  });

  it('hands the file to the share sheet with its type, as a separate action', async () => {
    mockEvent = eventWith([att('doc', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    const tree = render();

    await act(async () => press(tree, 'share:doc'));
    await flush();

    expect(mockShare).toHaveBeenCalledWith(expect.stringMatching(fileFor('ev-doc', 'pdf')), {
      mimeType: 'application/pdf',
    });
    expect(has(tree, 'viewer-pdf')).toBe(false);
  });

  // LAST in the file on purpose: it resets the module registry, and every
  // test after it would render the old screen against a new React.
  it('is never named again by a visit in a later process: the visit id is not a counter', async () => {
    // Process 1: share, leave. The file stays for the sheet.
    mockEvent = eventWith([att('doc', 'document')]);
    memfs.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    const tree = render();
    await act(async () => press(tree, 'share:doc'));
    await flush();
    const sharedUri = mockShare.mock.calls[0][0] as string;
    act(() => tree.unmount());
    await flush();
    expect(memfs.files.has(sharedUri)).toBe(true);

    // "Restart": a fresh module registry over the SAME file system (memfs
    // keeps its files on globalThis for exactly this). A counter would start
    // again at 1 and name the same file.
    jest.resetModules();
    /* eslint-disable @typescript-eslint/no-var-requires */
    const R2 = require('react') as typeof import('react');
    const RTR2 = require('react-test-renderer') as typeof import('react-test-renderer');
    const Screen2 = require('../../../app/site-log/[id]').default as typeof SiteLogRecordDetail;
    const auth2 = (require('../../store/auth') as typeof import('../../store/auth')).useAuthStore;
    const memfs2 = (require('./support/memfs') as typeof import('./support/memfs')).memfs;
    /* eslint-enable @typescript-eslint/no-var-requires */
    const flush2 = async () => {
      for (let i = 0; i < 8; i += 1) {
        // eslint-disable-next-line no-await-in-loop
        await RTR2.act(async () => {
          await Promise.resolve();
        });
      }
    };
    RTR2.act(() => {
      auth2.setState({ accessToken: 'tok-a', userId: 'user-a', sessionNonce: 1 });
    });
    memfs2.downloads.push({ status: 200, headers: { 'content-type': 'application/pdf' } });
    let tree2!: ReactTestRenderer;
    RTR2.act(() => {
      tree2 = RTR2.create(R2.createElement(Screen2));
    });
    await RTR2.act(async () => {
      (tree2.root.findByProps({ testID: 'view:doc' }).props as { onPress: () => void }).onPress();
    });
    await flush2();
    const shown = tree2.root.findByProps({ testID: 'viewer-pdf' }).props.source.uri as string;
    expect(shown).toMatch(fileFor('ev-doc', 'pdf'));
    expect(shown).not.toBe(sharedUri);

    RTR2.act(() => tree2.unmount());
    await flush2();
    // The new visit removed its own file and left the shared one alone.
    expect(memfs.files.has(shown)).toBe(false);
    expect(memfs.files.has(sharedUri)).toBe(true);
  });
});
