import { QueryClient } from '@tanstack/react-query'

/**
 * Shared QueryClient instance.
 *
 * Lives in its own module (rather than inline in main.tsx) so
 * non-component code can clear the cache at the auth boundary without
 * importing a route/component file — specifically the axios 401
 * interceptor's terminal-logout path (R12/H2) in src/api/client.ts.
 * Mirrors mobile/src/api/queryClient.ts (Audit B-02). Configuration is
 * unchanged from the original main.tsx inline client.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})
