import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

export default function LabourScreen() {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <View style={s.wrap}>
        <Text style={s.title}>{t('tabs.labour')}</Text>
        <Text style={s.body}>{t('settings.coming_soon')}</Text>
        <Text style={s.phase}>Phase 4</Text>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { fontSize: 22, fontWeight: '600', color: '#0f172a', marginBottom: 12 },
  body: { color: '#475569', fontSize: 16 },
  phase: { color: '#94a3b8', marginTop: 8, fontSize: 13 },
});
