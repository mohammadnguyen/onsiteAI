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

export type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  hydrated: boolean;
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

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  refreshToken: null,
  hydrated: false,
  hydrate: async () => {
    const [a, r] = await Promise.all([getItem(ACCESS_KEY), getItem(REFRESH_KEY)]);
    set({ accessToken: a, refreshToken: r, hydrated: true });
  },
  setTokens: async (a, r) => {
    await Promise.all([setItem(ACCESS_KEY, a), setItem(REFRESH_KEY, r)]);
    set({ accessToken: a, refreshToken: r });
  },
  setAccessToken: async (a) => {
    await setItem(ACCESS_KEY, a);
    set({ accessToken: a });
  },
  clear: async () => {
    await Promise.all([deleteItem(ACCESS_KEY), deleteItem(REFRESH_KEY)]);
    set({ accessToken: null, refreshToken: null });
  },
}));
