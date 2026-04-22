import type { ExpoConfig } from 'expo/config';
import base from './app.json';

const config = base.expo as ExpoConfig;
config.extra = {
  ...(config.extra ?? {}),
  apiUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000',
};

export default config;
