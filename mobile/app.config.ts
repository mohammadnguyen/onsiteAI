import type { ExpoConfig } from 'expo/config';
import base from './app.json';

const config = base.expo as ExpoConfig;

// M0 diagnostics: EAS injects EAS_BUILD_GIT_COMMIT_HASH during cloud
// builds. Locally (expo start / export) it is unset, so fall back to
// 'dev'. Only the short hash is embedded — surfaced on Settings →
// Diagnostics to identify exactly which commit a TestFlight build runs.
const easCommit = process.env.EAS_BUILD_GIT_COMMIT_HASH;

/**
 * Which app this build is.
 *
 * `default` is Forey - the one in daily use on the operator's phone. It is
 * untouched by everything below: same name, same bundle identifier, same
 * scheme, same API resolution it has always had.
 *
 * `test` is Forey Test: a SEPARATE app, so it installs alongside Forey
 * instead of over it. A different bundle identifier is what makes that
 * true, and it is also what separates their keychains, their document
 * directories and their local storage - the app sandbox is keyed to it.
 * Set with FOREY_VARIANT=test (the `test` build profile does).
 */
const VARIANT = process.env.FOREY_VARIANT ?? 'default';

/** Hosts that belong to the operator's real, in-use environments. */
const PROTECTED_HOSTS = ['sitetracker-backend-staging.fly.dev'];

if (VARIANT === 'test') {
  const url = process.env.EXPO_PUBLIC_API_URL;
  // No silent fallback. A test build with no API address would otherwise
  // resolve to localhost - unreachable from a phone - or, worse, be edited
  // later into pointing at the real backend by accident.
  if (!url || !/^https:\/\//.test(url)) {
    throw new Error(
      'Forey Test needs EXPO_PUBLIC_API_URL set to an https test API. ' +
        'Refusing to build a test app with no backend of its own.',
    );
  }
  const host = url.replace(/^https:\/\//, '').split('/')[0];
  if (PROTECTED_HOSTS.includes(host)) {
    throw new Error(
      `Forey Test must not point at ${host}: that backend carries real ` +
        'business data. Give it its own test API.',
    );
  }
  config.name = 'Forey Test';
  // Its own deep-link scheme: two installed apps claiming `forey://` would
  // leave iOS free to hand a link to either of them.
  config.scheme = 'foreytest';
  config.ios = { ...(config.ios ?? {}), bundleIdentifier: 'com.forey.app.test' };
  config.android = { ...(config.android ?? {}), package: 'com.forey.app.test' };
}

config.extra = {
  ...(config.extra ?? {}),
  apiUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000',
  buildCommit: easCommit ? easCommit.slice(0, 7) : 'dev',
  // Surfaced on Settings -> Diagnostics, so which app and which backend is
  // in front of you is answerable without guessing.
  variant: VARIANT,
};

export default config;
