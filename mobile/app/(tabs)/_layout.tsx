import { Tabs } from 'expo-router';
import { useTranslation } from 'react-i18next';

export default function TabsLayout() {
  const { t } = useTranslation();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: '#1e293b',
        tabBarInactiveTintColor: '#64748b',
      }}
    >
      <Tabs.Screen name="jobs" options={{ title: t('tabs.jobs') }} />
      <Tabs.Screen name="expenses" options={{ title: t('tabs.expenses') }} />
      <Tabs.Screen name="dashboard" options={{ title: t('tabs.dashboard') }} />
      <Tabs.Screen name="labour" options={{ title: t('tabs.labour') }} />
      <Tabs.Screen name="settings" options={{ title: t('tabs.settings') }} />
    </Tabs>
  );
}
