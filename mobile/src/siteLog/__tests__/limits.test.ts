/**
 * The attachment size limit, and the timeout that travels with it.
 *
 * Why these exist: a 26.9 MiB drawing was picked, copied into app
 * storage, declared, and refused only at upload - days later, on a retry,
 * as `size_cap`. Nothing before that point consulted a limit, and the two
 * sides did not share a number.
 */
// `expoConfig` is a non-configurable getter on the real module, so the
// config a build would carry is supplied through a mock instead.
const mockConfig: { extra: unknown } = { extra: undefined };
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    get expoConfig() {
      return mockConfig;
    },
  },
}));

import {
  DEFAULT_UPLOAD_LIMITS,
  exceedsLimit,
  formatBytes,
  uploadLimits,
} from '../limits';

function withExtra(extra: unknown) {
  mockConfig.extra = extra;
}

afterEach(() => {
  mockConfig.extra = undefined;
});

describe('uploadLimits', () => {
  it('uses what the variant configured', () => {
    withExtra({ uploadLimits: { maxUploadBytes: 52_428_800, uploadTimeoutMs: 300_000 } });
    expect(uploadLimits()).toEqual({
      maxUploadBytes: 52_428_800,
      uploadTimeoutMs: 300_000,
    });
  });

  it('falls back to the SMALLER pair when config says nothing', () => {
    // The safe direction. Falling back to the larger cap would let the
    // phone offer a file the server refuses - the original defect.
    withExtra({});
    expect(uploadLimits()).toEqual(DEFAULT_UPLOAD_LIMITS);
    expect(DEFAULT_UPLOAD_LIMITS.maxUploadBytes).toBe(26_214_400);
  });

  it('ignores nonsense rather than trusting it', () => {
    for (const bad of [0, -1, NaN, Infinity, '52428800', null, undefined, {}]) {
      withExtra({ uploadLimits: { maxUploadBytes: bad, uploadTimeoutMs: bad } });
      expect(uploadLimits()).toEqual(DEFAULT_UPLOAD_LIMITS);
    }
  });

  it('is exactly the byte count the Forey Test backend is set to', () => {
    // One number, both sides. If either moves without the other, a phone
    // offers what the server refuses.
    withExtra({ uploadLimits: { maxUploadBytes: 52_428_800, uploadTimeoutMs: 300_000 } });
    expect(uploadLimits().maxUploadBytes).toBe(50 * 1024 * 1024);
  });
});

describe('exceedsLimit', () => {
  const LIMIT = 52_428_800;

  it('catches the drawing that started this', () => {
    expect(exceedsLimit(28_163_249, 26_214_400)).toBe(true);
  });

  it('accepts that same drawing under the raised limit', () => {
    expect(exceedsLimit(28_163_249, LIMIT)).toBe(false);
  });

  it('is exclusive at the boundary - exactly the limit is allowed', () => {
    expect(exceedsLimit(LIMIT, LIMIT)).toBe(false);
    expect(exceedsLimit(LIMIT + 1, LIMIT)).toBe(true);
  });

  it('says nothing when the picker reported no size', () => {
    // A recording has no size until it stops. Guessing would refuse a file
    // that is fine; the server counts the bytes it receives instead.
    expect(exceedsLimit(null, LIMIT)).toBe(false);
    expect(exceedsLimit(undefined, LIMIT)).toBe(false);
    expect(exceedsLimit(NaN, LIMIT)).toBe(false);
  });
});

describe('formatBytes', () => {
  it('labels powers of two as MiB, because that is what they are', () => {
    // "50 MB" is 50,000,000 or 52,428,800 depending on who is speaking.
    // The message a user reads must not be the ambiguous one.
    expect(formatBytes(52_428_800)).toBe('50.0 MiB');
    expect(formatBytes(26_214_400)).toBe('25.0 MiB');
    expect(formatBytes(28_163_249)).toBe('26.9 MiB');
  });

  it('scales down sensibly', () => {
    expect(formatBytes(512)).toBe('512 bytes');
    expect(formatBytes(2048)).toBe('2.0 KiB');
    expect(formatBytes(1_500_000)).toBe('1.4 MiB');
  });

  it('does not pretend about nonsense', () => {
    expect(formatBytes(NaN)).toBe('?');
    expect(formatBytes(-1)).toBe('?');
  });
});
