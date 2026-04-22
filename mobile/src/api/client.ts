import axios, { AxiosError, AxiosInstance } from 'axios';
import Constants from 'expo-constants';
import { useAuthStore } from '../store/auth';

const extraApiUrl = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl;
const apiUrl: string =
  extraApiUrl ??
  process.env.EXPO_PUBLIC_API_URL ??
  'http://127.0.0.1:8000';

export const api: AxiosInstance = axios.create({
  baseURL: apiUrl,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
  }
  return config;
});

// Phase 1: on 401, clear tokens. The root layout's auth gate redirects to login.
// No refresh-token rotation — that's a Phase 6 concern.
api.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    if (err?.response?.status === 401) {
      await useAuthStore.getState().clear();
    }
    return Promise.reject(err);
  }
);
