import { canonicalHost, isProtectedHost } from '../../../../app.config';

/**
 * Where the live suite is allowed to write, and who it signs in as.
 *
 * This suite is not read-only: it declares captures, uploads bytes and
 * finalises records. Pointed at the wrong backend it would write the
 * founder's real business data. It previously took a single truthy
 * environment variable and hard-coded the local dev seed accounts, so one
 * mis-set variable was all that stood between it and production.
 *
 * The rule is an ALLOW-LIST, not a deny-list. A deny-list only refuses the
 * hosts someone thought of; anything it has not heard of is permitted, and
 * "the backend nobody listed" is exactly how real data gets written. Here
 * the target must be one of a named set, and the protected-host check runs
 * as well - so the real backend is refused twice, once by not being on the
 * list and once by name.
 *
 * Credentials come from the environment too. Test-account passwords do not
 * belong in the repository, and an absent one must stop the run rather than
 * fall back to a guess.
 */

/** Hosts this suite may write to. Nothing else, whatever the variable says. */
export const ALLOWED_LIVE_HOSTS = [
  'forey-test-api.fly.dev',
  'localhost',
  '127.0.0.1',
  '[::1]',
];

/** Loopback may be plain http; anything reached over a network may not. */
const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];

export type LiveAccount = { email: string; password: string };

export type LiveTarget = {
  baseUrl: string;
  admin: LiveAccount;
  /**
   * Deliberately NOT an admin: an admin may legitimately read another
   * author's record, so only an ordinary account tests the isolation the
   * listing is supposed to give.
   */
  other: LiveAccount;
};

export class LiveTargetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LiveTargetError';
  }
}

export type LiveEnv = Record<string, string | undefined>;

/** True when the operator asked for a live run at all. */
export function liveRunRequested(env: LiveEnv): boolean {
  const raw = env.SITE_LOG_LIVE_API;
  return typeof raw === 'string' && raw.trim() !== '';
}

function account(env: LiveEnv, prefix: string, label: string): LiveAccount {
  const email = (env[`${prefix}_EMAIL`] ?? '').trim();
  const password = env[`${prefix}_PASSWORD`] ?? '';
  if (email === '' || password === '') {
    throw new LiveTargetError(
      `The live suite needs ${prefix}_EMAIL and ${prefix}_PASSWORD for the ` +
        `${label} account on the test backend. Refusing to run writes with ` +
        'guessed credentials.',
    );
  }
  return { email, password };
}

/**
 * Resolve and validate the live target, or throw explaining why not.
 *
 * Throws rather than returning null on a bad configuration: a live suite
 * that quietly skips when it is misconfigured is indistinguishable from one
 * that passed, which is the reporting problem this whole change is about.
 */
export function resolveLiveTarget(env: LiveEnv): LiveTarget {
  const raw = (env.SITE_LOG_LIVE_API ?? '').trim();
  if (raw === '') {
    throw new LiveTargetError(
      'SITE_LOG_LIVE_API is not set. It must be the full base URL of the ' +
        'Forey Test backend, e.g. https://forey-test-api.fly.dev',
    );
  }

  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new LiveTargetError(
      `SITE_LOG_LIVE_API is "${raw}", which is not a URL. It must be the ` +
        'full base URL of the Forey Test backend.',
    );
  }

  const host = canonicalHost(url.hostname);

  // Checked first and separately, so the refusal says the true reason even
  // if the allow-list is ever edited carelessly.
  if (isProtectedHost(url.hostname)) {
    throw new LiveTargetError(
      `The live suite writes data and must never be pointed at ${host}: ` +
        'that backend carries real business data.',
    );
  }

  if (!ALLOWED_LIVE_HOSTS.map(canonicalHost).includes(host)) {
    throw new LiveTargetError(
      `${host} is not a backend the live suite may write to. Allowed: ` +
        `${ALLOWED_LIVE_HOSTS.join(', ')}.`,
    );
  }

  const loopback = LOOPBACK.map(canonicalHost).includes(host);
  if (url.protocol !== 'https:' && !(loopback && url.protocol === 'http:')) {
    throw new LiveTargetError(
      `${raw} must use https (http is allowed only for loopback).`,
    );
  }

  // Rebuilt from the canonical host rather than taken from `url.origin`:
  // origin keeps whatever spelling was typed in some URL implementations
  // (jsdom returns `https://FOREY-TEST-API.FLY.DEV`), and the client should
  // be given the same one form everything else here compares.
  const baseUrl = `${url.protocol}//${host}${url.port === '' ? '' : `:${url.port}`}`;

  return {
    baseUrl,
    admin: account(env, 'SITE_LOG_LIVE_ADMIN', 'admin'),
    other: account(env, 'SITE_LOG_LIVE_OTHER', 'contributor'),
  };
}
