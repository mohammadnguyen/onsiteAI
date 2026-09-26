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
 * scheme, same artwork, same API resolution it has always had.
 *
 * `test` is Forey Test: a SEPARATE app, so it installs alongside Forey
 * instead of over it. A different bundle identifier is what makes that
 * true, and it is also what separates their keychains, their document
 * directories and their local storage - the app sandbox is keyed to it.
 *
 * THE WHOLE SEPARATION HANGS ON ONE STRING, so it fails closed. Every
 * isolating property below sits behind `VARIANT === 'test'`; if that string
 * were simply absent on a cloud build, the test profile would quietly emit
 * a `com.forey.app` binary carrying the test backend's address. So on EAS
 * the variant must be stated, must be one this file knows, and must agree
 * with the build profile. Locally an absent variant still means Forey, so
 * ordinary development is unaffected.
 */
export const VARIANTS = ['default', 'test'] as const;
export type Variant = (typeof VARIANTS)[number];

/** The build profile whose identity is Forey Test, and only that one. */
export const TEST_PROFILE = 'test';

export type BuildEnvironment = {
  /** EAS sets EAS_BUILD=true on its builders; nothing else does. */
  onEasBuild: boolean;
  /** EAS_BUILD_PROFILE - the `--profile` the build was started with. */
  profile: string | undefined;
};

export function resolveVariant(
  raw: string | undefined,
  env: BuildEnvironment,
): Variant {
  const stated = raw === undefined ? '' : raw.trim();

  if (stated === '') {
    if (env.onEasBuild) {
      throw new Error(
        'FOREY_VARIANT is not set on this cloud build. Every EAS build ' +
          'profile must state its identity, because an unstated one means ' +
          'Forey - the real app. Add FOREY_VARIANT to this profile in ' +
          'eas.json.',
      );
    }
    // Local development: unstated means Forey, exactly as it always has.
    return 'default';
  }

  if (!(VARIANTS as readonly string[]).includes(stated)) {
    throw new Error(
      `FOREY_VARIANT is "${stated}", which is not a variant this app has. ` +
        `Expected one of: ${VARIANTS.join(', ')}. Refusing to guess which ` +
        'app to build.',
    );
  }
  const variant = stated as Variant;

  // The profile and the identity must agree. Checked only on EAS, where the
  // profile exists: locally there is no profile to disagree with.
  if (env.onEasBuild) {
    if (env.profile === TEST_PROFILE && variant !== 'test') {
      throw new Error(
        `The "${TEST_PROFILE}" build profile must build Forey Test, but ` +
          `FOREY_VARIANT is "${variant}". That combination would put the ` +
          "real app's identity on a test build.",
      );
    }
    if (env.profile !== TEST_PROFILE && variant === 'test') {
      throw new Error(
        'FOREY_VARIANT is "test" but the build profile is ' +
          `"${env.profile ?? '(none)'}". Forey Test is built only by the ` +
          `"${TEST_PROFILE}" profile, whose submit target is the Forey Test ` +
          'App Store Connect record.',
      );
    }
  }

  return variant;
}

/**
 * Hosts that belong to the operator's real, in-use environments.
 *
 * Stored canonical, compared canonical - see `canonicalHost`.
 */
const PROTECTED_HOSTS = ['sitetracker-backend-staging.fly.dev'];

/**
 * ONE spelling for a host.
 *
 * Two foldings, and neither is left to the runtime:
 *
 *  - TRAILING DOTS. `new URL()` keeps the root label's dot, so
 *    `sitetracker-backend-staging.fly.dev.` was one character away from the
 *    list entry and passed the check while naming the same machine. This is
 *    the bypass found in review.
 *  - UNICODE FULL STOPS. IDNA maps U+3002, U+FF0E and U+FF61 onto an
 *    ordinary dot. Node's URL does that mapping and Expo evaluates this
 *    file under Node, but jsdom's does not - a test proved the difference.
 *    A boundary this important must not depend on which URL implementation
 *    happens to parse it, so the mapping is done here.
 */
export function canonicalHost(hostname: string): string {
  return hostname
    .replace(/[。．｡]/g, '.')
    .toLowerCase()
    .replace(/\.+$/, '');
}

export function isProtectedHost(hostname: string): boolean {
  return PROTECTED_HOSTS.map(canonicalHost).includes(canonicalHost(hostname));
}

/**
 * The address a Forey Test build is allowed to carry, or an explanation.
 *
 * No silent fallback. A test build with no API address would otherwise
 * resolve to localhost - unreachable from a phone - or, worse, be edited
 * later into pointing at the real backend by accident.
 */
export function assertTestApiUrl(url: string | undefined): string {
  if (!url || !/^https:\/\//i.test(url)) {
    throw new Error(
      'Forey Test needs EXPO_PUBLIC_API_URL set to an https test API. ' +
        'Refusing to build a test app with no backend of its own.',
    );
  }
  let hostname: string;
  try {
    hostname = new URL(url).hostname;
  } catch {
    throw new Error(`Forey Test needs a valid https API url, not ${url}`);
  }
  if (isProtectedHost(hostname)) {
    throw new Error(
      `Forey Test must not point at ${canonicalHost(hostname)}: that backend ` +
        'carries real business data. Give it its own test API.',
    );
  }
  return url;
}

const VARIANT = resolveVariant(process.env.FOREY_VARIANT, {
  onEasBuild: process.env.EAS_BUILD === 'true',
  profile: process.env.EAS_BUILD_PROFILE,
});

if (VARIANT === 'test') {
  assertTestApiUrl(process.env.EXPO_PUBLIC_API_URL);

  config.name = 'Forey Test';
  // Its own deep-link scheme: two installed apps claiming `forey://` would
  // leave iOS free to hand a link to either of them.
  config.scheme = 'foreytest';
  config.ios = { ...(config.ios ?? {}), bundleIdentifier: 'com.forey.app.test' };
  config.android = { ...(config.android ?? {}), package: 'com.forey.app.test' };
  // Its own artwork. Without this the two apps are one icon under two
  // labels, and the home screen is where they have to be told apart - the
  // founder opened the wrong one during the first device session, and the
  // acceptance checklist warns that deleting the app destroys unsent
  // drafts. The production files are never touched; these are separate
  // assets that exist only for this variant.
  config.icon = './assets/icon-test.png';
  config.splash = {
    ...(config.splash ?? {}),
    image: './assets/splash-icon-test.png',
  };
  config.android = {
    ...config.android,
    adaptiveIcon: {
      ...(config.android?.adaptiveIcon ?? {}),
      foregroundImage: './assets/adaptive-icon-test.png',
    },
  };
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
