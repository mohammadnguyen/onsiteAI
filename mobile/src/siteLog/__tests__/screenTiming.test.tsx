/**
 * What happens when a submission finishes AFTER the user has moved on.
 *
 * These render the real screens with react-test-renderer - the renderer
 * jest-expo already installs - and drive the exact sequence that cost a
 * capture: submit, leave, then let the old submission finish. The screens
 * are the subject; nothing here tests a helper in isolation.
 *
 * It lives under src/, NOT under app/: Expo Router turns every .tsx file
 * in app/ into a route, so a test file there would be loaded - jest.mock
 * calls and all - when the app starts.
 *
 * "Left" is modelled as LOSING FOCUS, not unmounting, because that is the
 * case the app's own Stack produces: pushing a second capture on top keeps
 * this screen mounted underneath it, and `mockRouter.replace` acts on the
 * route that is current - the new one.
 */
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import React from 'react';

// ---- focus, controlled by the test --------------------------------------
const mockFocusCleanups: (() => void)[] = [];
function blurEverything(): void {
  while (mockFocusCleanups.length > 0) mockFocusCleanups.pop()?.();
}

const mockRouter = { replace: jest.fn(), push: jest.fn(), back: jest.fn(), canGoBack: () => true };
let mockSearchParams: Record<string, string> = {};

jest.mock('expo-router', () => ({
  // A getter: jest hoists this factory above the consts, and the screens are
  // imported (also hoisted) before `mockRouter` is initialised. Reading it
  // lazily is what makes the mock the real one by the time it is called.
  get router() {
    return mockRouter;
  },
  useLocalSearchParams: () => mockSearchParams,
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    // The real hook runs the effect on focus and its cleanup on blur.
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

const mockInvalidate = jest.fn();
jest.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: mockInvalidate }) }));

jest.mock('../../api/hooks/useAuth', () => ({
  useMe: () => ({ data: { user_id: 'user-a' } }),
}));
jest.mock('../../api/hooks/useJobs', () => ({ useJobs: () => ({ data: [] }) }));
jest.mock('../../components/JobPickerSheet', () => ({ JobPickerSheet: () => null }));

jest.mock('expo-image-picker', () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));
jest.mock('expo-document-picker', () => ({ getDocumentAsync: jest.fn() }));
jest.mock('expo-audio', () => ({
  RecordingPresets: { HIGH_QUALITY: {} },
  // `uri` is read through a holder so a test can make the recorder hand
  // back a real temp file; null keeps the previous behaviour by default.
  useAudioRecorder: () => ({
    prepareToRecordAsync: jest.fn(),
    record: jest.fn(),
    stop: jest.fn(),
    get uri() {
      return (globalThis as { __recorderUri?: string | null }).__recorderUri ?? null;
    },
  }),
  requestRecordingPermissionsAsync: jest.fn(),
  setAudioModeAsync: jest.fn(),
}));
jest.mock('expo-file-system/legacy', () => require('./support/memfs'));

// The button, made pressable without a gesture layer.
jest.mock('../../ui/kit', () => {
  const { Text } = require('react-native');
  return {
    PrimaryButton: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const react = require('react') as typeof import('react');
      return react.createElement(
        Text,
        { testID: `button:${label}`, onPress: disabled ? undefined : onPress },
        label,
      );
    },
    StatusBadge: () => null,
  };
});

// The dialog, captured so the test can dismiss it whenever it likes.
const mockNotify = jest.fn();
const mockConfirm = jest.fn();
jest.mock('../dialogs', () => ({
  notify: (args: unknown) => mockNotify(args),
  confirmDestructive: (args: unknown) => mockConfirm(args),
}));

// The submission itself: a promise this test resolves when it chooses.
type Outcome = Record<string, unknown>;
let resolveSubmit: (o: Outcome) => void = () => undefined;
const mockRunSubmit = jest.fn(
  () =>
    new Promise<Outcome>((resolve) => {
      resolveSubmit = resolve;
    }),
);
jest.mock('../submit', () => ({
  runSubmit: (...args: unknown[]) => mockRunSubmit(...(args as [])),
  currentUri: (a: { uri: string }) => a.uri,
}));

import { useSiteLogDrafts, type SiteLogDraft } from '../../store/siteLogDrafts';
import { useAuthStore } from '../../store/auth';
import NewSiteLogEntry from '../../../app/site-log/new';
import ResumeSiteLogDraft from '../../../app/site-log/draft/[captureClientId]';

const EVENT = { site_log_event_id: 'event-1' };

function draftFor(id: string): SiteLogDraft {
  return {
    capture_client_id: id,
    user_id: 'user-a',
    created_at: 1,
    updated_at: 1,
    declaration: null,
    body_text: 'Rain stopped work',
    job_id: null,
    attachments: [],
    server: null,
    unconfirmed: false,
    last_message: null,
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFocusCleanups.length = 0;
  mockSearchParams = {};
  useSiteLogDrafts.setState({ drafts: [], submitting: [] });
  useAuthStore.setState({ accessToken: 'a', refreshToken: 'r', userId: 'user-a', sessionNonce: 1 });
});

function typeInto(tree: ReactTestRenderer, text: string): void {
  const input = tree.root.findAllByType('TextInput' as unknown as React.ElementType)[0];
  act(() => {
    input.props.onChangeText(text);
  });
}

function press(tree: ReactTestRenderer, label: string): void {
  const button = tree.root.findByProps({ testID: `button:${label}` });
  act(() => {
    button.props.onPress();
  });
}

function pressId(tree: ReactTestRenderer, testID: string): void {
  const el = tree.root.findByProps({ testID });
  act(() => {
    el.props.onPress();
  });
}

describe('an attachment bigger than the limit', () => {
  // The drawing that started this: picked, copied into app storage,
  // declared, and refused only at upload days later with `size_cap`. The
  // refusal has to happen here, before any of that.
  const OVERSIZED = 60 * 1024 * 1024; // over even the raised 50 MiB cap

  it('is refused before it is copied or recorded', async () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const DocumentPicker = require('expo-document-picker') as {
      getDocumentAsync: jest.Mock;
    };
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: 'file:///cache/huge.dwg',
          name: 'huge.dwg',
          mimeType: 'application/dwg',
          size: OVERSIZED,
        },
      ],
    });
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { memfs } = require('./support/memfs') as typeof import('./support/memfs');
    memfs.reset();
    memfs.put('file:///cache/huge.dwg', OVERSIZED);

    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });

    pressId(tree, 'attach-document');
    await flush();

    // Nothing copied into the app's own area...
    const kept = [...memfs.files.keys()].filter((p) => p.includes('/site-log/'));
    expect(kept).toEqual([]);
    // ...and nothing recorded on the screen.
    expect(tree.root.findAllByProps({ testID: 'attachment-row' })).toHaveLength(0);
  });

  it('is refused after the copy too, when the picker reported no size', async () => {
    // The gap the founder found by reading the code: the pick-time check
    // can only run on a size the picker gave, and a recording has none
    // until it stops - the very attachment most likely to grow past the
    // limit unnoticed. The copy measures it, so the answer exists by
    // then, and the copy must not survive the refusal.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const DocumentPicker = require('expo-document-picker') as {
      getDocumentAsync: jest.Mock;
    };
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: 'file:///cache/unknown.m4a',
          name: 'unknown.m4a',
          mimeType: 'audio/mp4',
          size: undefined, // the picker does not know
        },
      ],
    });
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { memfs } = require('./support/memfs') as typeof import('./support/memfs');
    memfs.reset();
    memfs.put('file:///cache/unknown.m4a', OVERSIZED);

    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });

    pressId(tree, 'attach-document');
    await flush();

    // Copied, measured, refused - and the copy cleaned up behind it.
    const kept = [...memfs.files.keys()].filter((p) => p.includes('/site-log/'));
    expect(kept).toEqual([]);
    expect(tree.root.findAllByProps({ testID: 'attachment-row' })).toHaveLength(0);
  });

  it('REGRESSION: an oversized RECORDING keeps its bytes', async () => {
    // A recording cannot be made again. The refusal introduced with the
    // size limit deleted the kept copy, which for a recording is the only
    // durable one - the recorder's own file is temporary and the OS may
    // reclaim it. Refusing must never cost the evidence.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const audio = require('expo-audio') as {
      requestRecordingPermissionsAsync: jest.Mock;
      setAudioModeAsync: jest.Mock;
    };
    audio.requestRecordingPermissionsAsync.mockResolvedValue({ granted: true });
    audio.setAudioModeAsync.mockResolvedValue(undefined);

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { memfs } = require('./support/memfs') as typeof import('./support/memfs');
    memfs.reset();
    memfs.put('file:///cache/rec.m4a', OVERSIZED);
    (globalThis as { __recorderUri?: string | null }).__recorderUri =
      'file:///cache/rec.m4a';

    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });

    // Start, then stop - the mocked recorder hands back the temp file.
    pressId(tree, 'record-voice');
    await flush();
    pressId(tree, 'record-voice');
    await flush();

    // Not attached, because an over-cap attachment would make the whole
    // capture undeclarable...
    expect(tree.root.findAllByProps({ testID: 'attachment-row' })).toHaveLength(0);
    // ...but the durable copy is STILL THERE. This is the assertion that
    // fails if the release is ever made unconditional again.
    // Kept - and OUTSIDE the capture's own directory, which is the only
    // way "kept" means anything: every cleanup path deletes that
    // directory whole.
    const kept = [...memfs.files.keys()].filter((p) => p.includes('/site-log/'));
    expect(kept).toHaveLength(1);
    expect(kept[0]).toContain('/oversized/');
    expect(kept[0]).not.toMatch(/\/site-log\/[^/]+\/[0-9a-f-]{36}\//);

    // Codex's point: the earlier version stopped before cleanup and
    // missed the loss entirely. Both cleanup paths are now exercised
    // UNCONDITIONALLY - an earlier attempt guarded them on a captureId
    // that was always undefined, so neither actually ran.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const files = require('../files') as typeof import('../files');
    const preservedPath = kept[0];

    // 1. Leaving the screen: releaseCapture on a real capture directory
    //    that also holds a file, so the call is proved to do something.
    const capture = 'cap-cleanup-1';
    memfs.put(`file:///documents/site-log/user-a/${capture}/att.jpg`, 10);
    await act(async () => {
      tree.unmount();
      await files.releaseCapture('user-a', capture);
    });
    expect(memfs.files.has(`file:///documents/site-log/user-a/${capture}/att.jpg`)).toBe(
      false,
    );
    expect(memfs.files.has(preservedPath)).toBe(true);

    // 2. A successful save: removeAndRelease, with a draft that really
    //    exists so the call cannot be a no-op.
    const saved = 'cap-cleanup-2';
    memfs.put(`file:///documents/site-log/user-a/${saved}/att.jpg`, 10);
    await act(async () => {
      await useSiteLogDrafts.getState().upsertDurable({
        ...draftFor(saved),
        user_id: 'user-a',
      });
      await useSiteLogDrafts.getState().removeAndRelease(saved);
    });
    expect(useSiteLogDrafts.getState().get(saved)).toBeUndefined();
    expect(memfs.files.has(`file:///documents/site-log/user-a/${saved}/att.jpg`)).toBe(false);

    // The preserved recording outlived both.
    const survivors = [...memfs.files.keys()].filter((p) => p.includes('/site-log/'));
    expect(survivors).toEqual([preservedPath]);
    expect(survivors[0]).toContain('/oversized/');

    (globalThis as { __recorderUri?: string | null }).__recorderUri = null;
  });

  it('lets a file inside the limit through', async () => {
    // The same path, proving the refusal is not simply "never add
    // anything".
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const DocumentPicker = require('expo-document-picker') as {
      getDocumentAsync: jest.Mock;
    };
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: 'file:///cache/small.dwg',
          name: 'small.dwg',
          mimeType: 'application/dwg',
          size: 3_563_298, // the 3.4 MiB drawing that really did upload
        },
      ],
    });
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { memfs } = require('./support/memfs') as typeof import('./support/memfs');
    memfs.reset();
    memfs.put('file:///cache/small.dwg', 3_563_298);

    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });

    pressId(tree, 'attach-document');
    await flush();

    const kept = [...memfs.files.keys()].filter((p) => p.includes('/site-log/'));
    expect(kept).toHaveLength(1);
  });
});

describe('the capture screen, when the user leaves mid-submission', () => {
  it('does not navigate away from whatever they opened next', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });
    typeInto(tree, 'Poured bay 3');
    press(tree, 'siteLog.new.submit');
    await flush();
    expect(mockRunSubmit).toHaveBeenCalledTimes(1);

    // The user goes back and starts another capture: this screen keeps
    // running underneath, and the route in front of them is not this one.
    blurEverything();

    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it('still finishes its own bookkeeping: the record is not left holding a draft', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });
    typeInto(tree, 'Poured bay 3');
    press(tree, 'siteLog.new.submit');
    await flush();

    const captureId = useSiteLogDrafts.getState().drafts[0]?.capture_client_id;
    expect(captureId).toBeDefined();

    blurEverything();
    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    // Confirmed saved: the draft goes, whether or not anyone is watching.
    expect(useSiteLogDrafts.getState().get(captureId as string)).toBeUndefined();
    expect(mockInvalidate).toHaveBeenCalled();
    expect(useSiteLogDrafts.getState().submitting).toEqual([]);
  });

  it('DOES navigate when the user is still on it - so the guard is not just "never"', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });
    typeInto(tree, 'Poured bay 3');
    press(tree, 'siteLog.new.submit');
    await flush();

    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    expect(mockRouter.replace).toHaveBeenCalledWith('/site-log/event-1');
  });

  it('does not answer a submission belonging to an account that has signed out', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });
    typeInto(tree, 'Poured bay 3');
    press(tree, 'siteLog.new.submit');
    await flush();

    // Somebody else signed in while it was running.
    act(() => {
      useAuthStore.setState({ sessionNonce: 2 });
    });
    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it('will not let a dialog dismissed later carry the user off a new screen', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(NewSiteLogEntry));
    });
    typeInto(tree, 'Poured bay 3');
    press(tree, 'siteLog.new.submit');
    await flush();

    await act(async () => {
      resolveSubmit({ kind: 'partial', event: EVENT, failed: ['att-1'], blocked: [] });
      await Promise.resolve();
    });
    await flush();

    expect(mockNotify).toHaveBeenCalledTimes(1);
    const onOk = (mockNotify.mock.calls[0][0] as { onOk: () => void }).onOk;

    // The user leaves before dismissing it.
    blurEverything();
    act(() => {
      onOk();
    });

    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});

describe('the resume screen, when the user leaves mid-submission', () => {
  beforeEach(() => {
    mockSearchParams = { captureClientId: 'capture-9' };
    useSiteLogDrafts.setState({ drafts: [draftFor('capture-9')], submitting: [] });
  });

  it('does not navigate away from whatever they opened next', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(ResumeSiteLogDraft));
    });
    press(tree, 'siteLog.draft.resume');
    await flush();
    expect(mockRunSubmit).toHaveBeenCalledTimes(1);

    blurEverything();
    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    expect(mockRouter.replace).not.toHaveBeenCalled();
    // The draft it owned is still released: that is its own record.
    expect(useSiteLogDrafts.getState().get('capture-9')).toBeUndefined();
  });

  it('DOES navigate when the user is still on it', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(ResumeSiteLogDraft));
    });
    press(tree, 'siteLog.draft.resume');
    await flush();

    await act(async () => {
      resolveSubmit({ kind: 'complete', event: EVENT });
      await Promise.resolve();
    });
    await flush();

    expect(mockRouter.replace).toHaveBeenCalledWith('/site-log/event-1');
  });

  it('will not let a dialog dismissed later carry the user off a new screen', async () => {
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(React.createElement(ResumeSiteLogDraft));
    });
    press(tree, 'siteLog.draft.resume');
    await flush();

    await act(async () => {
      resolveSubmit({ kind: 'partial', event: EVENT, failed: [], blocked: [] });
      await Promise.resolve();
    });
    await flush();

    const onOk = (mockNotify.mock.calls[0][0] as { onOk: () => void }).onOk;
    blurEverything();
    act(() => {
      onOk();
    });

    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});
