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
  useAudioRecorder: () => ({ prepareToRecordAsync: jest.fn(), record: jest.fn(), stop: jest.fn(), uri: null }),
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
