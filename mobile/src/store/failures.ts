import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

/**
 * M0: persistent failed-capture visibility store.
 *
 * When a capture POST fails (network drop, validation 422, server
 * error) the submitted text + the user-facing error message are
 * recorded here so the failure stays visible after form reset and
 * across app restarts. Previously the only trace was a transient
 * `formError` banner that vanished on reset/restart — the typed text
 * was silently lost.
 *
 * Persistence contract (operator-approved M0 scope):
 * - Stores ONLY failed-capture metadata: the capture text the user
 *   typed, the error message shown, a context tag, and a timestamp.
 *   NEVER tokens, credentials, auth material, or raw backend response
 *   payloads.
 * - Bounded: at most MAX_FAILURES entries (newest first); older
 *   entries are dropped so local storage cannot grow without limit.
 * - Backend: AsyncStorage (NOT SecureStore — this is non-secret
 *   operational data; SecureStore stays reserved for tokens per
 *   src/store/auth.ts).
 */

export type CaptureFailureContext = 'single' | 'multi' | 'app';

export type CaptureFailure = {
  id: string;
  /** Epoch ms when the failure was recorded. */
  ts: number;
  /** The capture text the user submitted ('' for app-level errors). */
  inputText: string;
  /** User-facing error message shown at failure time. */
  errorMessage: string;
  context: CaptureFailureContext;
};

type FailuresState = {
  failures: CaptureFailure[];
  recordFailure: (f: {
    inputText: string;
    errorMessage: string;
    context: CaptureFailureContext;
  }) => void;
  dismissFailure: (id: string) => void;
  clearFailures: () => void;
};

/** Hard cap on stored entries — keeps the persisted store bounded. */
const MAX_FAILURES = 20;

export const useFailuresStore = create<FailuresState>()(
  persist(
    (set) => ({
      failures: [],
      recordFailure: (f) =>
        set((state) => ({
          failures: [
            {
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              ts: Date.now(),
              ...f,
            },
            ...state.failures,
          ].slice(0, MAX_FAILURES),
        })),
      dismissFailure: (id) =>
        set((state) => ({
          failures: state.failures.filter((f) => f.id !== id),
        })),
      clearFailures: () => set({ failures: [] }),
    }),
    {
      name: 'sitetracker-capture-failures',
      storage: createJSONStorage(() => AsyncStorage),
      version: 1,
    },
  ),
);
