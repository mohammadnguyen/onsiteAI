import Constants from 'expo-constants';

/**
 * How big an attachment may be, and how long its upload may take.
 *
 * ONE NUMBER, IN BYTES, ON BOTH SIDES. A 26.9 MiB drawing was picked,
 * copied into app storage, declared, and only refused at upload with
 * `size_cap` - after a retry, days later. Nothing before that point knew
 * there was a limit. Stating it in bytes and comparing it in bytes is
 * deliberate: "50 MB" is 50,000,000 or 52,428,800 depending on who is
 * speaking, and a phone that allows what the server refuses reproduces
 * exactly the failure this exists to prevent.
 *
 * PER VARIANT, because the two apps talk to different backends. Forey
 * Test's backend is configured to 52,428,800; the real Forey's is
 * unchanged at 26,214,400, so the app in daily use is not affected by any
 * of this. The pair travels with the variant in `app.config.ts`, and the
 * timeout moves with the cap because they constrain the same upload: the
 * larger cap needs the longer window to be reachable at all.
 *
 * THIS IS A CLIENT COURTESY, NOT THE ENFORCEMENT. The server counts the
 * bytes it actually receives and refuses past its own limit whatever a
 * client declared. A size the picker does not report - a recording has
 * none until it is stopped - simply cannot be checked here, and is caught
 * there.
 */
export type UploadLimits = {
  /** Largest attachment this app will offer to send, in bytes. */
  maxUploadBytes: number;
  /** Per-request upload timeout, in milliseconds. */
  uploadTimeoutMs: number;
};

/**
 * What the real Forey uses, and the fallback when config is unreadable.
 *
 * Failing back to the SMALLER pair is the safe direction: it refuses a
 * file the server might have taken, which is recoverable by asking, where
 * the opposite silently reproduces the original defect.
 */
export const DEFAULT_UPLOAD_LIMITS: UploadLimits = {
  maxUploadBytes: 26_214_400, // 25 MiB
  uploadTimeoutMs: 180_000,
};

function positiveInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.floor(value)
    : null;
}

export function uploadLimits(): UploadLimits {
  const configured = (
    Constants.expoConfig?.extra as { uploadLimits?: Partial<UploadLimits> } | undefined
  )?.uploadLimits;
  return {
    maxUploadBytes:
      positiveInt(configured?.maxUploadBytes) ?? DEFAULT_UPLOAD_LIMITS.maxUploadBytes,
    uploadTimeoutMs:
      positiveInt(configured?.uploadTimeoutMs) ?? DEFAULT_UPLOAD_LIMITS.uploadTimeoutMs,
  };
}

/**
 * Bytes as a person reads them, labelled honestly.
 *
 * MiB, not MB, and said so. The limit is a power-of-two number; calling
 * 52,428,800 bytes "50 MB" is how a phone and a server end up disagreeing
 * about the same file.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '?';
  if (bytes < 1024) return `${Math.round(bytes)} bytes`;
  const kib = bytes / 1024;
  if (kib < 1024) return `${kib.toFixed(kib < 10 ? 1 : 0)} KiB`;
  const mib = kib / 1024;
  return `${mib.toFixed(mib < 100 ? 1 : 0)} MiB`;
}

/** True when the picker reported a size and that size is already too big. */
export function exceedsLimit(size: number | null | undefined, limit: number): boolean {
  return typeof size === 'number' && Number.isFinite(size) && size > limit;
}
