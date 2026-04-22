import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const isWeb = Platform.OS === 'web';

// Tiny async storage shim covering the single language-preference key. Uses
// expo-secure-store on native and localStorage on web (with SSR / privacy-mode
// safety). No SecureStore on web because the module throws at import time.
const storage = {
  getItem: async (k: string): Promise<string | null> => {
    if (isWeb) {
      try {
        return globalThis.localStorage?.getItem(k) ?? null;
      } catch {
        return null;
      }
    }
    return await SecureStore.getItemAsync(k);
  },
  setItem: async (k: string, v: string): Promise<void> => {
    if (isWeb) {
      try {
        globalThis.localStorage?.setItem(k, v);
      } catch {
        // no-op
      }
      return;
    }
    await SecureStore.setItemAsync(k, v);
  },
};

export default storage;
