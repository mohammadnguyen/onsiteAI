import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

/**
 * Dashboard placeholder.
 *
 * Mobile Polish slice (Half A): replaced the developer-flavoured
 * "Phase 3" badge with a user-facing body line, since this screen is
 * what real builder / admin users see if they tap the tab. The
 * tab itself stays — removing it is a separate routing decision.
 */
export default function DashboardScreen() {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <View style={s.wrap}>
        <Text style={s.title}>{t('tabs.dashboard')}</Text>
        <Text style={s.body}>{t('common.unavailable_in_this_version')}</Text>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { fontSize: 22, fontWeight: '600', color: '#0f172a', marginBottom: 12 },
  body: { color: '#475569', fontSize: 16, textAlign: 'center', lineHeight: 22 },
});
