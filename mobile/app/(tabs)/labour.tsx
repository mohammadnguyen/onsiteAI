import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

/**
 * Labour placeholder.
 *
 * Mobile Polish slice (Half A): replaced the developer-flavoured
 * "Phase 4" badge with a user-facing body line, matching the
 * Dashboard placeholder treatment.
 */
export default function LabourScreen() {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <View style={s.wrap}>
        <Text style={s.title}>{t('tabs.labour')}</Text>
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
