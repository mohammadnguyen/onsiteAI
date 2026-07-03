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
      refetchOnWindowFocus: false,
    },
  },
});
