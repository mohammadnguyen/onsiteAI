import { useEffect, useState } from 'react';
import { Slot, useRouter, useSegments } from 'expo-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { useAuthStore } from '../src/store/auth';
import { initI18n } from '../src/i18n';

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

export default function RootLayout() {
  const [ready, setReady] = useState(false);
  const hydrated = useAuthStore((s) => s.hydrated);
  const hydrate = useAuthStore((s) => s.hydrate);
  const accessToken = useAuthStore((s) => s.accessToken);
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    (async () => {
      try {
        await initI18n();
      } catch {
        // i18n failure is non-fatal; continue with defaults.
      }
      await hydrate();
      setReady(true);
    })();
  }, [hydrate]);

  useEffect(() => {
    if (!ready || !hydrated) return;
    const first = segments[0];
    const inAuth = first === '(auth)';
    if (!accessToken && !inAuth) {
      router.replace('/(auth)/login');
    } else if (accessToken && (inAuth || first === undefined)) {
      router.replace('/(tabs)/jobs');
    }
  }, [ready, hydrated, accessToken, segments, router]);

  if (!ready) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator size="large" color="#1e293b" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <QueryClientProvider client={qc}>
        <StatusBar style="auto" />
        <Slot />
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#ffffff',
  },
});
