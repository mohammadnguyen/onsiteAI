import { useEffect, useState } from 'react';
import {
  Stack,
  useRouter,
  useSegments,
  type ErrorBoundaryProps,
  type Href,
} from 'expo-router';
import { QueryClientProvider, focusManager } from '@tanstack/react-query';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import {
  AppState,
  View,
  Text,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { useAuthStore } from '../src/store/auth';
import i18n, { initI18n } from '../src/i18n';
import { useFailuresStore } from '../src/store/failures';
import { useFontScaleStore } from '../src/store/fontScale';
import { queryClient } from '../src/api/queryClient';
import { resetSessionState } from '../src/store/session';

/**
 * M0: global JS error hook.
 *
 * React Native funnels uncaught JS errors through `ErrorUtils`. We wrap
 * the previously-installed handler — never replace it — so the
 * platform's own behaviour (dev red box, native crash handling) is
 * unchanged; we only add a bounded, non-secret record to the persisted
 * failures store so a field crash leaves a visible trace after restart
 * (the entry shows up on the Capture screen's failed-captures list).
 *
 * `ErrorUtils` is a React Native global; on web it is undefined and
 * registration is a safe no-op — the exported ErrorBoundary below
 * still catches render errors there.
 */
type GlobalErrorHandler = (error: unknown, isFatal?: boolean) => void;
type ErrorUtilsLike = {
  getGlobalHandler?: () => GlobalErrorHandler | undefined;
  setGlobalHandler?: (handler: GlobalErrorHandler) => void;
};

let globalErrorHandlerInstalled = false;

function registerGlobalErrorHandler(): void {
  if (globalErrorHandlerInstalled) return;
  const errorUtils = (globalThis as { ErrorUtils?: ErrorUtilsLike }).ErrorUtils;
  if (!errorUtils?.getGlobalHandler || !errorUtils.setGlobalHandler) return;
  const previous = errorUtils.getGlobalHandler();
  errorUtils.setGlobalHandler((error, isFatal) => {
    try {
      const message =
        error instanceof Error
          ? error.message
          : String(error ?? 'Unknown error');
      useFailuresStore.getState().recordFailure({
        inputText: '',
        errorMessage: message,
        context: 'app',
      });
    } catch {
      // Recording must never interfere with platform error handling.
    }
    previous?.(error, isFatal);
  });
  globalErrorHandlerInstalled = true;
}

registerGlobalErrorHandler();

/**
 * M0: translate-with-fallback for the error screen.
 *
 * The ErrorBoundary must render even when the crash happened before
 * i18n finished initialising, so it cannot use the useTranslation hook
 * (which can suspend pre-init). Post-init this returns the normal
 * bilingual string; pre-init it falls back to English. The literal
 * fallbacks exist ONLY for this worst-case path.
 */
function tSafe(key: string, fallback: string): string {
  try {
    if (i18n.isInitialized) {
      const value = i18n.t(key);
      if (typeof value === 'string' && value !== key) return value;
    }
  } catch {
    // fall through to the literal fallback
  }
  return fallback;
}

/**
 * M0: app-level error boundary (expo-router convention — exporting
 * `ErrorBoundary` from the root layout catches render/runtime errors
 * for the whole route tree). Renders a safe fallback with a retry
 * button. Shows `error.message` only — never a stack trace, request
 * data, or anything token-bearing — and records the failure in the
 * persisted store for post-restart visibility.
 */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  useEffect(() => {
    useFailuresStore.getState().recordFailure({
      inputText: '',
      errorMessage: error.message || String(error),
      context: 'app',
    });
  }, [error]);

  return (
    <View style={styles.errorWrap} testID="app-error-boundary">
      <Text style={styles.errorTitle}>
        {tSafe('error_boundary.title', 'Something went wrong')}
      </Text>
      <Text style={styles.errorMessage}>
        {tSafe(
          'error_boundary.message',
          'The app hit an unexpected error. Please try again.',
        )}
      </Text>
      {error.message ? (
        <Text style={styles.errorDetail} numberOfLines={4}>
          {error.message}
        </Text>
      ) : null}
      <TouchableOpacity
        onPress={retry}
        style={styles.errorRetryBtn}
        testID="app-error-retry"
        accessibilityRole="button"
      >
        <Text style={styles.errorRetryText}>
          {tSafe('error_boundary.retry', 'Try again')}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

export default function RootLayout() {
  const [ready, setReady] = useState(false);
  const hydrated = useAuthStore((s) => s.hydrated);
  const hydrate = useAuthStore((s) => s.hydrate);
  const accessToken = useAuthStore((s) => s.accessToken);
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    (async () => {
      try {
        await initI18n();
      } catch {
        // i18n failure is non-fatal; continue with defaults.
      }
      // O3 (U5): restore the font-size preference alongside language.
      // Non-fatal — the store defaults to 'standard'.
      // B-01: BOTH hydrate awaits are guarded — a keychain/AsyncStorage
      // read rejection must never strand the launch on the splash
      // spinner. Failure = start logged-out with default font size.
      try {
        await useFontScaleStore.getState().hydrate();
      } catch {
        // defaults apply
      }
      try {
        await hydrate();
      } catch {
        // Auth store stays empty -> normal logged-out flow. MUST still
        // flip `hydrated`, or the auth-redirect effect below never runs
        // and the launch strands on a blank screen anyway.
        useAuthStore.setState({ hydrated: true });
      }
      setReady(true);
    })();
  }, [hydrate]);

  // X-2: drive React Query's focusManager from AppState so returning
  // to the app from the background counts as a "window focus" —
  // combined with refetchOnWindowFocus (queryClient.ts), money
  // surfaces refetch on resume instead of showing another device's
  // stale numbers until a mutation happens to invalidate them.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      // Ignore iOS 'inactive' — it fires on share sheets, Face ID,
      // the notification shade and the photo picker. Counting those
      // as focus-loss would turn every capture-with-share round trip
      // into a full refetch burst on a weak site network. Only a real
      // background/foreground transition toggles focus.
      if (state === 'inactive') return;
      focusManager.setFocused(state === 'active');
    });
    return () => sub.remove();
  }, []);

  useEffect(() => {
    if (!ready || !hydrated) return;
    const first = segments[0];
    const inAuth = first === '(auth)';
    if (!accessToken && !inAuth) {
      // Audit B-02: crossing the auth boundary — manual logout,
      // terminal 401, dead refresh token — wipes user-scoped state
      // (React Query cache, cross-screen selections) so the next
      // login on a shared device can't read the previous user's
      // data. This is the single choke point every logout path
      // funnels through. Failed-capture texts are deliberately NOT
      // wiped here: an involuntary logout (token death mid-shift) is
      // almost certainly the SAME user, whose typed-but-unsent
      // capture texts must survive; the explicit Settings logout
      // wipes them separately. On a logged-out cold start this fires
      // once as a no-op on empty state.
      resetSessionState();
      // dismissAll pops the root stack to its base entry before the
      // replace. Without it, replace() only swaps the FOCUSED route:
      // a terminal 401 on a pushed screen would leave the old
      // session's (tabs) — component state included — mounted UNDER
      // the login screen, reachable by iOS edge-swipe and surviving
      // into the next login as a stale duplicate stack entry.
      if (router.canDismiss()) router.dismissAll();
      router.replace('/(auth)/login');
    } else if (accessToken && (inAuth || first === undefined)) {
      router.replace('/(tabs)/home' as unknown as Href);
    }
  }, [ready, hydrated, accessToken, segments, router]);

  if (!ready) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator size="large" color="#1e293b" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <StatusBar style="auto" />
        {/* Root Stack (was Slot — back-nav fix): a Stack keeps the
            screens BENEATH a pushed route mounted, so (tabs) preserves
            the active tab across drill-ins. With Slot, every sibling
            unmounted on push and the tab bar remounted on its first
            tab — back from any pushed screen landed on Expenses
            regardless of where the user came from. Headers stay off:
            every pushed screen renders its own back button. */}
        <Stack screenOptions={{ headerShown: false }} />
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#ffffff',
  },
  errorWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#ffffff',
    padding: 24,
  },
  errorTitle: {
    fontSize: 20,
    fontWeight: '600',
    color: '#0f172a',
    marginBottom: 8,
    textAlign: 'center',
  },
  errorMessage: {
    fontSize: 14,
    color: '#475569',
    textAlign: 'center',
    marginBottom: 8,
  },
  errorDetail: {
    fontSize: 12,
    color: '#94a3b8',
    textAlign: 'center',
    marginBottom: 20,
  },
  errorRetryBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    paddingHorizontal: 32,
    borderRadius: 6,
  },
  errorRetryText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
