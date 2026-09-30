import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const ACCESS_KEY = 'sitetracker_access';
const REFRESH_KEY = 'sitetracker_refresh';

// expo-secure-store isn't available on web — fall back to localStorage.
const isWeb = Platform.OS === 'web';

async function setItem(k: string, v: string): Promise<void> {
  if (isWeb) {
    try {
      globalThis.localStorage?.setItem(k, v);
    } catch {
      // localStorage may be unavailable (SSR, privacy mode). Silently ignore.
    }
    return;
  }
  await SecureStore.setItemAsync(k, v);
}

async function getItem(k: string): Promise<string | null> {
  if (isWeb) {
    try {
      return globalThis.localStorage?.getItem(k) ?? null;
    } catch {
      return null;
    }
  }
  return await SecureStore.getItemAsync(k);
}

async function deleteItem(k: string): Promise<void> {
  if (isWeb) {
    try {
      globalThis.localStorage?.removeItem(k);
    } catch {
      // no-op
    }
    return;
  }
  await SecureStore.deleteItemAsync(k);
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

/**
 * The `sub` claim of an access token: the id of the account it was issued
 * to.
 *
 * NOT a security check and never used as one - the signature is not
 * verified here and cannot be, because the phone has no key. The server
 * decides what this account may see. It exists so the app knows WHOSE
 * unsent drafts to show when it cannot reach /auth/me: on a cold start
 * with no signal, the worker whose text is sitting on the phone must still
 * be able to find it.
 *
 * Decoded by hand rather than with atob, which is not guaranteed on every
 * JS runtime this app ships to.
 */
export function userIdFromAccessToken(token: string | null): string | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const raw = parts[1];
    let bits = 0;
    let acc = 0;
    let out = '';
    for (const ch of raw) {
      const value = B64.indexOf(ch);
      if (value < 0) continue; // padding or a stray character
      acc = (acc << 6) | value;
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        out += String.fromCharCode((acc >> bits) & 0xff);
      }
    }
    const claims = JSON.parse(out) as { sub?: unknown };
    return typeof claims.sub === 'string' && claims.sub.length > 0 ? claims.sub : null;
  } catch {
    return null;
  }
}

export type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  hydrated: boolean;
  /**
   * The signed-in account's id, read from the access token.
   *
   * Available before - and without - a successful /auth/me, which is what
   * makes the unsent-draft list work on a phone with no signal.
   */
  userId: string | null;
  /**
   * Which signed-in session this is. Incremented whenever the tokens are
   * replaced or cleared - a sign-in or a sign-out - and NOT by the refresh
   * interceptor, which renews the same session's access token.
   *
   * It exists so long-running work can tell that the account changed under
   * it. Comparing tokens would not do: a refresh rotates the access token
   * within one session, and the value itself is a credential.
   */
  sessionNonce: number;
  hydrate: () => Promise<void>;
  setTokens: (a: string, r: string) => Promise<void>;
  /**
   * Update ONLY the access token in both SecureStore and in-memory
   * store. Used by the axios response interceptor's refresh-on-401
   * flow: the backend `/auth/refresh` route returns just a new access
   * token (the refresh token remains valid until its 30-day TTL), so
   * `setTokens` would be misleading here.
   */
  setAccessToken: (a: string) => Promise<void>;
  clear: () => Promise<void>;
};

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  refreshToken: null,
  hydrated: false,
  userId: null,
  sessionNonce: 0,
  hydrate: async () => {
    const [a, r] = await Promise.all([getItem(ACCESS_KEY), getItem(REFRESH_KEY)]);
    set({
      accessToken: a,
      refreshToken: r,
      userId: userIdFromAccessToken(a),
      hydrated: true,
    });
  },
  setTokens: async (a, r) => {
    await Promise.all([setItem(ACCESS_KEY, a), setItem(REFRESH_KEY, r)]);
    set({
      accessToken: a,
      refreshToken: r,
      userId: userIdFromAccessToken(a),
      sessionNonce: get().sessionNonce + 1,
    });
  },
  setAccessToken: async (a) => {
    await setItem(ACCESS_KEY, a);
    // Same session, same account: the nonce does not move.
    set({ accessToken: a, userId: userIdFromAccessToken(a) });
  },
  clear: async () => {
    await Promise.all([deleteItem(ACCESS_KEY), deleteItem(REFRESH_KEY)]);
    set({
      accessToken: null,
      refreshToken: null,
      userId: null,
      sessionNonce: get().sessionNonce + 1,
    });
  },
}));
