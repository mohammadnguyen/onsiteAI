import { Tabs } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { AppTabBar } from '../../src/ui/AppTabBar';

/**
 * UI-kit v2 Batch 2 (operator-approved IA): three tabs + central ➕.
 *  - Home (new): admin stats cards + entry rows (Settings lives here).
 *  - Jobs / Labour: unchanged screens.
 *  - Capture is NO LONGER a tab: the AppTabBar's central ➕ pushes
 *    /capture onto the root stack.
 *  - Settings is NO LONGER a tab: pushed from Home as /settings.
 * AppTabBar assumes this exact screen order (home, jobs, labour).
 */
export default function TabsLayout() {
  const { t } = useTranslation();
  return (
    <Tabs
      tabBar={(props) => <AppTabBar {...props} />}
      screenOptions={{ headerShown: false }}
    >
      <Tabs.Screen name="home" options={{ title: t('tabs.home') }} />
      <Tabs.Screen name="jobs" options={{ title: t('tabs.jobs') }} />
      <Tabs.Screen name="labour" options={{ title: t('tabs.labour') }} />
    </Tabs>
  );
}
