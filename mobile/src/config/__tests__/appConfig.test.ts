/**
 * The app-identity boundary.
 *
 * app.config.ts decides which of two apps a build is. Everything that keeps
 * Forey Test away from the founder's real Forey - the bundle identifier,
 * the name, the scheme, the artwork, and the refusal to point at the real
 * backend - sits behind one string. Until now that string had no test at
 * all, in a suite of 109.
 *
 * Two kinds of test here, deliberately:
 *  - the pure decisions, called directly, so every branch is cheap to cover;
 *  - the module itself, evaluated under a set environment, so the wiring
 *    between those decisions and the emitted config is covered too. A
 *    correct rule wired to nothing would pass the first kind alone.
 */
import {
  assertTestApiUrl,
  canonicalHost,
  isProtectedHost,
  resolveVariant,
  TEST_PROFILE,
  VARIANTS,
} from '../../../app.config';

const LOCAL = { onEasBuild: false, profile: undefined };
const EAS = (profile: string | undefined) => ({ onEasBuild: true, profile });

const REAL_BACKEND = 'sitetracker-backend-staging.fly.dev';
const TEST_BACKEND = 'forey-test-api.fly.dev';

describe('resolveVariant - local development', () => {
  it('treats an unstated variant as Forey, as it always has', () => {
    expect(resolveVariant(undefined, LOCAL)).toBe('default');
    expect(resolveVariant('', LOCAL)).toBe('default');
    expect(resolveVariant('   ', LOCAL)).toBe('default');
  });

  it('still builds Forey Test when asked, with no profile to agree with', () => {
    expect(resolveVariant('test', LOCAL)).toBe('test');
    expect(resolveVariant('  test  ', LOCAL)).toBe('test');
  });

  it('refuses a variant it does not know, rather than guessing', () => {
    // The dangerous default is Forey, so an unrecognised value must never
    // fall through to it.
    for (const bogus of ['Test', 'TEST', 'prod', 'staging', 'true', '1']) {
      expect(() => resolveVariant(bogus, LOCAL)).toThrow(/not a variant/);
    }
  });
});

describe('resolveVariant - cloud builds fail closed', () => {
  it('refuses a cloud build that does not state its identity', () => {
    expect(() => resolveVariant(undefined, EAS(TEST_PROFILE))).toThrow(
      /FOREY_VARIANT is not set/,
    );
    expect(() => resolveVariant('', EAS('production'))).toThrow(
      /FOREY_VARIANT is not set/,
    );
  });

  it('accepts the two agreeing combinations', () => {
    expect(resolveVariant('test', EAS(TEST_PROFILE))).toBe('test');
    expect(resolveVariant('default', EAS('production'))).toBe('default');
    expect(resolveVariant('default', EAS('preview'))).toBe('default');
  });

  it('refuses the test profile carrying the real app identity', () => {
    // This is the defect the whole check exists for: drop FOREY_VARIANT
    // from the test profile and a com.forey.app binary ships with the test
    // backend's address in it.
    expect(() => resolveVariant('default', EAS(TEST_PROFILE))).toThrow(
      /must build Forey Test/,
    );
  });

  it('refuses the test identity from any other profile', () => {
    // The other direction: the test identity built by a profile whose
    // submit target is the real App Store Connect record.
    expect(() => resolveVariant('test', EAS('production'))).toThrow(
      /built only by the "test" profile/,
    );
    expect(() => resolveVariant('test', EAS('preview'))).toThrow(
      /built only by the "test" profile/,
    );
    expect(() => resolveVariant('test', EAS(undefined))).toThrow(
      /built only by the "test" profile/,
    );
  });

  it('knows exactly two variants', () => {
    // A third would need its own profile agreement rule; this fails loudly
    // if one is added without revisiting the checks above.
    expect([...VARIANTS]).toEqual(['default', 'test']);
  });
});

describe('canonicalHost', () => {
  it('folds the spellings that name the same machine', () => {
    expect(canonicalHost('EXAMPLE.COM')).toBe('example.com');
    expect(canonicalHost('example.com.')).toBe('example.com');
    expect(canonicalHost('example.com...')).toBe('example.com');
    expect(canonicalHost('EXAMPLE.COM.')).toBe('example.com');
  });

  it('leaves a different host different', () => {
    expect(canonicalHost('a.example.com')).not.toBe(canonicalHost('example.com'));
  });
});

describe('isProtectedHost', () => {
  it('recognises the real backend however it is spelled', () => {
    expect(isProtectedHost(REAL_BACKEND)).toBe(true);
    expect(isProtectedHost(REAL_BACKEND.toUpperCase())).toBe(true);
    // The bypass found in review: one trailing dot, same machine.
    expect(isProtectedHost(`${REAL_BACKEND}.`)).toBe(true);
    expect(isProtectedHost(`${REAL_BACKEND}..`)).toBe(true);
  });

  it('does not over-reach onto the test backend or a neighbour', () => {
    expect(isProtectedHost(TEST_BACKEND)).toBe(false);
    expect(isProtectedHost(`evil-${REAL_BACKEND}`)).toBe(false);
    expect(isProtectedHost(`x.${REAL_BACKEND}`)).toBe(false);
  });
});

describe('assertTestApiUrl', () => {
  it('requires an https address at all', () => {
    expect(() => assertTestApiUrl(undefined)).toThrow(/needs EXPO_PUBLIC_API_URL/);
    expect(() => assertTestApiUrl('')).toThrow(/needs EXPO_PUBLIC_API_URL/);
    expect(() => assertTestApiUrl(`http://${TEST_BACKEND}`)).toThrow(
      /needs EXPO_PUBLIC_API_URL/,
    );
    expect(() => assertTestApiUrl('not a url')).toThrow(/needs EXPO_PUBLIC_API_URL/);
  });

  it('refuses the real backend in every equivalent spelling', () => {
    const spellings = [
      `https://${REAL_BACKEND}`,
      `https://${REAL_BACKEND}/`,
      `https://${REAL_BACKEND}/api/v1`,
      `https://${REAL_BACKEND}?x=1`,
      `https://${REAL_BACKEND}:443`,
      `https://${REAL_BACKEND.toUpperCase()}`,
      `HTTPS://${REAL_BACKEND}`,
      `https://user:pw@${REAL_BACKEND}`,
      // The trailing-dot bypass, and it combined with the other spellings.
      `https://${REAL_BACKEND}.`,
      `https://${REAL_BACKEND}./`,
      `https://${REAL_BACKEND}.:443/api`,
      `https://${REAL_BACKEND.toUpperCase()}.`,
      // A unicode full stop, which URL parsing maps onto an ASCII one.
      'https://sitetracker-backend-staging.fly。dev',
    ];
    // Collected rather than asserted one at a time: a failure then names
    // the spelling that got through, which is the only useful thing to
    // know here.
    const accepted = spellings.filter((url) => {
      try {
        assertTestApiUrl(url);
        return true;
      } catch {
        return false;
      }
    });
    expect(accepted).toEqual([]);
  });

  it('accepts the test backend', () => {
    expect(assertTestApiUrl(`https://${TEST_BACKEND}`)).toBe(`https://${TEST_BACKEND}`);
    expect(assertTestApiUrl(`https://${TEST_BACKEND}/`)).toBe(`https://${TEST_BACKEND}/`);
  });
});

/**
 * The module itself. These prove the decisions above are actually wired to
 * the config Expo reads - name, identifier, scheme and artwork - rather
 * than merely being correct in isolation.
 */
describe('the emitted config', () => {
  const saved = { ...process.env };

  afterEach(() => {
    process.env = { ...saved };
    jest.resetModules();
  });

  function load(env: Record<string, string | undefined>) {
    process.env = { ...saved, ...env };
    // Each load re-evaluates app.config.ts under this environment.
    let cfg: Record<string, unknown> | undefined;
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      cfg = require('../../../app.config').default;
    });
    return cfg as Record<string, unknown>;
  }

  it('is Forey when nothing is stated', () => {
    const cfg = load({
      FOREY_VARIANT: undefined,
      EAS_BUILD: undefined,
      EAS_BUILD_PROFILE: undefined,
    });
    expect(cfg.name).toBe('Forey');
    expect(cfg.scheme).toBe('forey');
    expect((cfg.ios as { bundleIdentifier: string }).bundleIdentifier).toBe(
      'com.forey.app',
    );
    // Production artwork is whatever app.json says, never a test asset.
    expect(String(cfg.icon)).not.toContain('test');
    expect((cfg.extra as { variant: string }).variant).toBe('default');
  });

  it('is Forey Test, with its own artwork, when the test profile builds it', () => {
    const cfg = load({
      FOREY_VARIANT: 'test',
      EAS_BUILD: 'true',
      EAS_BUILD_PROFILE: 'test',
      EXPO_PUBLIC_API_URL: `https://${TEST_BACKEND}`,
    });
    expect(cfg.name).toBe('Forey Test');
    expect(cfg.scheme).toBe('foreytest');
    expect((cfg.ios as { bundleIdentifier: string }).bundleIdentifier).toBe(
      'com.forey.app.test',
    );
    expect(cfg.icon).toBe('./assets/icon-test.png');
    expect((cfg.splash as { image: string }).image).toBe(
      './assets/splash-icon-test.png',
    );
    expect((cfg.extra as { variant: string }).variant).toBe('test');
    expect((cfg.extra as { apiUrl: string }).apiUrl).toBe(`https://${TEST_BACKEND}`);
  });

  it('will not build at all when a cloud build omits the variant', () => {
    expect(() =>
      load({
        FOREY_VARIANT: undefined,
        EAS_BUILD: 'true',
        EAS_BUILD_PROFILE: 'test',
        EXPO_PUBLIC_API_URL: `https://${TEST_BACKEND}`,
      }),
    ).toThrow(/FOREY_VARIANT is not set/);
  });

  it('will not build a test app aimed at the real backend', () => {
    expect(() =>
      load({
        FOREY_VARIANT: 'test',
        EAS_BUILD: 'true',
        EAS_BUILD_PROFILE: 'test',
        EXPO_PUBLIC_API_URL: `https://${REAL_BACKEND}.`,
      }),
    ).toThrow(/must not point at/);
  });
});
