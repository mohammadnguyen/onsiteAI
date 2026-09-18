import { AxiosError } from 'axios';
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';

/**
 * The 401 refresh-and-retry path, exercised through the real interceptor.
 *
 * What matters here is not that a refresh works - it did before - but that
 * a refresh belonging to one signed-in session can never act on another.
 * Two accounts share a phone; one person's request must not be replayed
 * with the next person's token, and one person's stale failure must not
 * sign the next person out.
 */

type Reply = { status: number; data?: unknown };
type Handler = (config: InternalAxiosRequestConfig) => Reply;

function load(handler: Handler) {
  jest.resetModules();
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { api } = require('../client') as typeof import('../client');
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { useAuthStore } = require('../../store/auth') as typeof import('../../store/auth');

  const sent: { url: string; auth?: string }[] = [];
  const adapter: AxiosAdapter = async (config) => {
    sent.push({
      url: String(config.url),
      auth: (config.headers as Record<string, string> | undefined)?.Authorization,
    });
    const reply = handler(config as InternalAxiosRequestConfig);
    const response = {
      data: reply.data ?? {},
      status: reply.status,
      statusText: '',
      headers: {},
      config,
    };
    if (reply.status >= 200 && reply.status < 300) return response;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    throw new AxiosError('failed', undefined, config as any, undefined, response as any);
  };
  api.defaults.adapter = adapter;

  useAuthStore.setState({
    accessToken: 'access-A',
    refreshToken: 'refresh-A',
    hydrated: true,
    sessionNonce: 1,
  });
  return { api, useAuthStore, sent };
}

describe('401 handling', () => {
  it('refreshes once and retries the original request with the new token', async () => {
    let refreshed = false;
    const { api, sent, useAuthStore } = load((config) => {
      if (String(config.url).endsWith('/auth/refresh')) {
        refreshed = true;
        return { status: 200, data: { access_token: 'access-A2', token_type: 'bearer' } };
      }
      return refreshed ? { status: 200, data: { ok: true } } : { status: 401 };
    });

    const r = await api.get('/jobs');

    expect(r.status).toBe(200);
    expect(sent.map((s) => s.url)).toEqual(['/jobs', '/auth/refresh', '/jobs']);
    expect(sent[2].auth).toBe('Bearer access-A2');
    expect(useAuthStore.getState().accessToken).toBe('access-A2');
  });

  it('does not refresh or retry a request whose account has changed', async () => {
    const { api, sent, useAuthStore } = load((config) => {
      if (String(config.url) === '/jobs') {
        // While this request was in flight, A signed out and B signed in.
        useAuthStore.setState({
          accessToken: 'access-B',
          refreshToken: 'refresh-B',
          sessionNonce: 2,
        });
        return { status: 401 };
      }
      return { status: 200 };
    });

    await expect(api.get('/jobs')).rejects.toBeInstanceOf(AxiosError);

    // No refresh, no replay: B's token must not carry A's request.
    expect(sent.map((s) => s.url)).toEqual(['/jobs']);
    // And B is still signed in.
    expect(useAuthStore.getState().accessToken).toBe('access-B');
    expect(useAuthStore.getState().refreshToken).toBe('refresh-B');
  });

  it('signs the session out when its own refresh token is dead', async () => {
    const { api, useAuthStore } = load((config) =>
      String(config.url).endsWith('/auth/refresh') ? { status: 401 } : { status: 401 },
    );

    await expect(api.get('/jobs')).rejects.toBeInstanceOf(AxiosError);

    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it('does not sign out the account that arrived while a doomed refresh was in flight', async () => {
    const { api, useAuthStore } = load((config) => {
      if (String(config.url).endsWith('/auth/refresh')) {
        // A's refresh was rejected - but by now this is B's phone.
        useAuthStore.setState({
          accessToken: 'access-B',
          refreshToken: 'refresh-B',
          sessionNonce: 2,
        });
        return { status: 401 };
      }
      return { status: 401 };
    });

    await expect(api.get('/jobs')).rejects.toBeInstanceOf(AxiosError);

    expect(useAuthStore.getState().accessToken).toBe('access-B');
    expect(useAuthStore.getState().refreshToken).toBe('refresh-B');
  });
});

describe('the session identity itself', () => {
  it('changes on sign-in and on sign-out, and not on a token refresh', async () => {
    const { useAuthStore } = load(() => ({ status: 200 }));
    const start = useAuthStore.getState().sessionNonce;

    await useAuthStore.getState().setAccessToken('access-A3');
    expect(useAuthStore.getState().sessionNonce).toBe(start);

    await useAuthStore.getState().setTokens('access-B', 'refresh-B');
    expect(useAuthStore.getState().sessionNonce).toBe(start + 1);

    await useAuthStore.getState().clear();
    expect(useAuthStore.getState().sessionNonce).toBe(start + 2);
  });
});
