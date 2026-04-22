import { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'expo-router';
import i18n, { setLanguage, type Lang } from '../../src/i18n';
import { useAuthStore } from '../../src/store/auth';
import { api } from '../../src/api/client';
import { useMe } from '../../src/api/hooks/useAuth';

export default function SettingsScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const clear = useAuthStore((s) => s.clear);
  const { data: me, isLoading } = useMe();
  const [lang, setLang] = useState<Lang>((i18n.language as Lang) || 'en');

  useEffect(() => {
    const handler = (l: string) => setLang((l as Lang) || 'en');
    i18n.on('languageChanged', handler);
    return () => {
      i18n.off('languageChanged', handler);
    };
  }, []);

  const pickLang = async (next: Lang) => {
    if (next === lang) return;
    await setLanguage(next);
  };

  const onLogout = async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // logout is best-effort — clear local state regardless.
    }
    await clear();
    router.replace('/(auth)/login');
  };

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <View style={s.wrap}>
        <Text style={s.title}>{t('settings.title')}</Text>

        <View style={s.card}>
          <Text style={s.cardLabel}>{t('settings.signed_in_as')}</Text>
          {isLoading ? (
            <ActivityIndicator color="#1e293b" />
          ) : (
            <Text style={s.cardValue} testID="settings-user-email">
              {me?.email ?? '-'}
            </Text>
          )}
        </View>

        <View style={s.card}>
          <Text style={s.cardLabel}>{t('settings.language')}</Text>
          <View style={s.langRow}>
            <TouchableOpacity
              style={[s.langBtn, lang === 'en' && s.langBtnActive]}
              onPress={() => pickLang('en')}
              testID="settings-lang-en"
              accessibilityRole="button"
            >
              <Text style={[s.langBtnText, lang === 'en' && s.langBtnTextActive]}>
                {t('settings.english')}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[s.langBtn, lang === 'zh' && s.langBtnActive]}
              onPress={() => pickLang('zh')}
              testID="settings-lang-zh"
              accessibilityRole="button"
            >
              <Text style={[s.langBtnText, lang === 'zh' && s.langBtnTextActive]}>
                {t('settings.chinese')}
              </Text>
            </TouchableOpacity>
          </View>
        </View>

        <TouchableOpacity
          style={s.logoutBtn}
          onPress={onLogout}
          testID="settings-logout"
          accessibilityRole="button"
        >
          <Text style={s.logoutText}>{t('settings.logout')}</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  wrap: { flex: 1, padding: 20 },
  title: { fontSize: 22, fontWeight: '600', color: '#0f172a', marginBottom: 20 },
  card: {
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    padding: 14,
    marginBottom: 14,
    backgroundColor: '#ffffff',
  },
  cardLabel: { color: '#64748b', fontSize: 13, marginBottom: 6 },
  cardValue: { color: '#0f172a', fontSize: 16 },
  langRow: { flexDirection: 'row', gap: 8 },
  langBtn: {
    flex: 1,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    alignItems: 'center',
    backgroundColor: '#f8fafc',
  },
  langBtnActive: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  langBtnText: { color: '#0f172a', fontWeight: '500' },
  langBtnTextActive: { color: '#ffffff' },
  logoutBtn: {
    marginTop: 12,
    padding: 14,
    borderRadius: 6,
    backgroundColor: '#b91c1c',
    alignItems: 'center',
  },
  logoutText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
