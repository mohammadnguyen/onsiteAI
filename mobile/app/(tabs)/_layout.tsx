import { Tabs } from 'expo-router';
import { useTranslation } from 'react-i18next';

export default function TabsLayout() {
  const { t } = useTranslation();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: '#1e293b',
        tabBarInactiveTintColor: '#64748b',
        // Mobile Smoke Patch 1: suppress the default placeholder tab
        // icons (small triangle / dot glyphs Expo Router falls back to
        // when no `tabBarIcon` is supplied). Returning null collapses
        // the icon slot so each tab renders label-only. No new
        // dependency required; if/when we want real icons later, swap
        // to @expo/vector-icons (bundled with Expo, still zero new
        // deps) in a separate change.
        tabBarIcon: () => null,
      }}
    >
      <Tabs.Screen name="expenses" options={{ title: t('tabs.expenses') }} />
      <Tabs.Screen name="jobs" options={{ title: t('tabs.jobs') }} />
      <Tabs.Screen name="dashboard" options={{ title: t('tabs.dashboard') }} />
      <Tabs.Screen name="labour" options={{ title: t('tabs.labour') }} />
      <Tabs.Screen name="settings" options={{ title: t('tabs.settings') }} />
    </Tabs>
  );
}
