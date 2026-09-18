import { randomUUID } from 'expo-crypto';

/**
 * A v4 UUID that works on the devices this app actually runs on.
 *
 * `globalThis.crypto.randomUUID` is not available under Hermes and this app
 * installs no polyfill, so reaching for it throws at the first render of the
 * capture screen. expo-crypto provides the native implementation.
 */
export function newCaptureId(): string {
  return randomUUID();
}
