/**
 * The guard on the live WRITE suite.
 *
 * Tested in the ordinary hermetic suite on purpose: the guard has to be
 * known-good even on the runs where the live suite itself does not execute,
 * which is nearly all of them. An untested guard on a suite that writes to
 * a real backend is not a guard.
 */
import {
  ALLOWED_LIVE_HOSTS,
  liveRunRequested,
  resolveLiveTarget,
} from './live/liveTarget';

const REAL_BACKEND = 'sitetracker-backend-staging.fly.dev';
const TEST_BACKEND = 'forey-test-api.fly.dev';

const CREDS = {
  SITE_LOG_LIVE_ADMIN_EMAIL: 'admin@forey-test.example.com',
  SITE_LOG_LIVE_ADMIN_PASSWORD: 'x',
  SITE_LOG_LIVE_OTHER_EMAIL: 'worker@forey-test.example.com',
  SITE_LOG_LIVE_OTHER_PASSWORD: 'y',
};

const env = (over: Record<string, string | undefined>) => ({ ...CREDS, ...over });

describe('liveRunRequested', () => {
  it('is false unless a target is actually named', () => {
    expect(liveRunRequested({})).toBe(false);
    expect(liveRunRequested({ SITE_LOG_LIVE_API: '' })).toBe(false);
    expect(liveRunRequested({ SITE_LOG_LIVE_API: '   ' })).toBe(false);
    expect(liveRunRequested({ SITE_LOG_LIVE_API: `https://${TEST_BACKEND}` })).toBe(true);
  });
});

describe('resolveLiveTarget refuses the real backend', () => {
  it('by name, in every equivalent spelling', () => {
    const spellings = [
      `https://${REAL_BACKEND}`,
      `https://${REAL_BACKEND}/`,
      `https://${REAL_BACKEND}:443`,
      `https://${REAL_BACKEND.toUpperCase()}`,
      `https://${REAL_BACKEND}.`,
      `https://user:pw@${REAL_BACKEND}`,
      'https://sitetracker-backend-staging.fly。dev',
    ];
    const accepted = spellings.filter((url) => {
      try {
        resolveLiveTarget(env({ SITE_LOG_LIVE_API: url }));
        return true;
      } catch {
        return false;
      }
    });
    expect(accepted).toEqual([]);
  });

  it('with a message naming it as carrying real business data', () => {
    expect(() =>
      resolveLiveTarget(env({ SITE_LOG_LIVE_API: `https://${REAL_BACKEND}` })),
    ).toThrow(/real business data/);
  });
});

describe('resolveLiveTarget is an allow-list, not a deny-list', () => {
  it('refuses a backend nobody thought to forbid', () => {
    // The whole reason for an allow-list: this host is on no deny-list
    // anywhere, and it is still not somewhere a write suite may go.
    expect(() =>
      resolveLiveTarget(env({ SITE_LOG_LIVE_API: 'https://api.some-other-env.example.com' })),
    ).toThrow(/is not a backend the live suite may write to/);
  });

  it('refuses a host that merely looks like the test backend', () => {
    for (const host of [
      `evil-${TEST_BACKEND}`,
      `${TEST_BACKEND}.evil.example.com`,
      `x.${TEST_BACKEND}`,
    ]) {
      expect(() => resolveLiveTarget(env({ SITE_LOG_LIVE_API: `https://${host}` }))).toThrow(
        /is not a backend/,
      );
    }
  });

  it('accepts the Forey Test backend, trailing dot and case included', () => {
    for (const url of [
      `https://${TEST_BACKEND}`,
      `https://${TEST_BACKEND}/`,
      `https://${TEST_BACKEND}.`,
      `https://${TEST_BACKEND.toUpperCase()}`,
    ]) {
      expect(resolveLiveTarget(env({ SITE_LOG_LIVE_API: url })).baseUrl).toContain(
        'forey-test-api.fly.dev',
      );
    }
  });

  it('allows loopback for local development, over http only there', () => {
    expect(resolveLiveTarget(env({ SITE_LOG_LIVE_API: 'http://localhost:8000' })).baseUrl).toBe(
      'http://localhost:8000',
    );
    expect(resolveLiveTarget(env({ SITE_LOG_LIVE_API: 'http://127.0.0.1:8000' })).baseUrl).toBe(
      'http://127.0.0.1:8000',
    );
    // Not loopback: plain http over a network is refused.
    expect(() =>
      resolveLiveTarget(env({ SITE_LOG_LIVE_API: `http://${TEST_BACKEND}` })),
    ).toThrow(/must use https/);
  });

  it('lists only hosts that are deliberate', () => {
    expect(ALLOWED_LIVE_HOSTS).toEqual([
      'forey-test-api.fly.dev',
      'localhost',
      '127.0.0.1',
      '[::1]',
    ]);
  });
});

describe('resolveLiveTarget requires real credentials', () => {
  it('refuses to guess them', () => {
    const base = { SITE_LOG_LIVE_API: `https://${TEST_BACKEND}` };
    expect(() => resolveLiveTarget({ ...base })).toThrow(/SITE_LOG_LIVE_ADMIN_EMAIL/);
    expect(() =>
      resolveLiveTarget({
        ...base,
        SITE_LOG_LIVE_ADMIN_EMAIL: 'a@b.c',
        SITE_LOG_LIVE_ADMIN_PASSWORD: 'x',
      }),
    ).toThrow(/SITE_LOG_LIVE_OTHER_EMAIL/);
  });

  it('returns both accounts once they are given', () => {
    const t = resolveLiveTarget(env({ SITE_LOG_LIVE_API: `https://${TEST_BACKEND}` }));
    expect(t.admin.email).toBe('admin@forey-test.example.com');
    expect(t.other.email).toBe('worker@forey-test.example.com');
  });
});

describe('resolveLiveTarget on a malformed target', () => {
  it('explains rather than falling back to anything', () => {
    expect(() => resolveLiveTarget(env({ SITE_LOG_LIVE_API: '1' }))).toThrow(/not a URL/);
    expect(() => resolveLiveTarget(env({ SITE_LOG_LIVE_API: 'true' }))).toThrow(/not a URL/);
    // The old contract was a truthy flag; anyone still passing one gets a
    // clear error instead of a run against the default base URL.
    expect(() => resolveLiveTarget(env({ SITE_LOG_LIVE_API: 'yes' }))).toThrow(/not a URL/);
    expect(() => resolveLiveTarget(env({}))).toThrow(/is not set/);
  });
});
