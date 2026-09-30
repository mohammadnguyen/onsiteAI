/* eslint-disable */
// Native modules the app touches at import time. Each test that cares about
// one of these overrides it with jest.mock; these defaults only stop an
// import from throwing.

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

jest.mock('expo-crypto', () => {
  let n = 0;
  return {
    randomUUID: () => {
      n += 1;
      const hex = n.toString(16).padStart(12, '0');
      return `00000000-0000-4000-8000-${hex}`;
    },
  };
});

// Backed by a global map on purpose: jest.resetModules() gives a test a new
// module registry, which is how "the app restarted" is expressed - and a
// restart must find the data still there. A per-module mock would come back
// empty and the restart test would prove nothing.
jest.mock('@react-native-async-storage/async-storage', () => {
  const g = globalThis;
  if (!g.__asyncStorageData) g.__asyncStorageData = new Map();
  const data = g.__asyncStorageData;
  return {
    __esModule: true,
    default: {
      getItem: async (k) => (data.has(k) ? data.get(k) : null),
      setItem: async (k, v) => {
        // Deferred by one turn, like a real write. Without this the mock
        // lands the value synchronously and "awaited the write" cannot be
        // told apart from "fired the write and carried on" - which is the
        // difference the durability tests exist to pin.
        await new Promise((resolve) => setTimeout(resolve, 0));
        data.set(k, String(v));
      },
      removeItem: async (k) => {
        data.delete(k);
      },
      clear: async () => {
        data.clear();
      },
      getAllKeys: async () => [...data.keys()],
      multiGet: async (ks) => ks.map((k) => [k, data.has(k) ? data.get(k) : null]),
      multiSet: async (pairs) => {
        pairs.forEach(([k, v]) => data.set(k, String(v)));
      },
      multiRemove: async (ks) => {
        ks.forEach((k) => data.delete(k));
      },
    },
  };
});
