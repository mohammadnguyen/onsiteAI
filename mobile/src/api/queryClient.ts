import { QueryClient } from '@tanstack/react-query';

/**
 * Shared QueryClient instance.
 *
 * Lives in its own module (rather than inline in app/_layout.tsx) so
 * non-component code — specifically the logout session reset in
 * src/store/session.ts (audit B-02) — can clear the cache without
 * importing a route file. Configuration is unchanged from the
 * original _layout inline client.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      // X-2: was false. With the AppState -> focusManager wiring in
      // app/_layout.tsx, "window focus" now means "app returned to the
      // foreground" — stale queries (most money surfaces use
      // staleTime 0) refetch on resume, so a phone reopened on site
      // shows other devices' captures without waiting for a local
      // mutation. staleTime is still honoured (e.g. the expenses list's
      // 30s), so this is not a refetch storm.
      refetchOnWindowFocus: true,
    },
  },
});
