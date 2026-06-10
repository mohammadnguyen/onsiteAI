import type { ExpoConfig } from 'expo/config';
import base from './app.json';

const config = base.expo as ExpoConfig;

// M0 diagnostics: EAS injects EAS_BUILD_GIT_COMMIT_HASH during cloud
// builds. Locally (expo start / export) it is unset, so fall back to
// 'dev'. Only the short hash is embedded — surfaced on Settings →
// Diagnostics to identify exactly which commit a TestFlight build runs.
const easCommit = process.env.EAS_BUILD_GIT_COMMIT_HASH;

config.extra = {
  ...(config.extra ?? {}),
  apiUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000',
  buildCommit: easCommit ? easCommit.slice(0, 7) : 'dev',
};

export default config;
