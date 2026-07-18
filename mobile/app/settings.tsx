import { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useRouter, type Href } from 'expo-router';
import Constants from 'expo-constants';
import i18n, { setLanguage, type Lang } from '../src/i18n';
import { useAuthStore } from '../src/store/auth';
import {
  useFontScaleStore,
  type FontScaleLevel,
} from '../src/store/fontScale';
import { useScaledStyles } from '../src/ui/type';
import { api, apiUrl } from '../src/api/client';
import { useMe } from '../src/api/hooks/useAuth';
import { resetSessionState, wipeFailures } from '../src/store/session';
import { useOneShotBack } from '../src/util/navigation';
import { tokens } from '../src/ui/tokens';

export default function SettingsScreen() {
  const { t } = useTranslation();
  // O3 (U5): this screen both hosts the font-size control and scales
  // itself, so the choice is visible the moment it's tapped.
  const s = useScaledStyles(base);
  const fontLevel = useFontScaleStore((st) => st.level);
  const setFontLevel = useFontScaleStore((st) => st.setLevel);
  const router = useRouter();
  // B2 (IA rework): settings is a PUSHED screen (entered from Home).
  const onBack = useOneShotBack('/(tabs)/home' as unknown as Href);
  const clear = useAuthStore((s) => s.clear);
  const { data: me, isLoading } = useMe();
  // M4: admin-only Users entry — /auth/me drives VISIBILITY ONLY;
  // the /users backend routes stay authoritative (403 for
  // contributors). Hidden while the role loads (fails closed).
  const isAdmin = me?.role === 'admin';
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
    // Audit B-02: explicit logout is a deliberate device handoff —
    // wipe user-scoped caches AND the persisted failed-capture
    // texts here, deterministically (the root layout's auth-redirect
    // reset also fires, but is idempotent and deliberately preserves
    // failures for INVOLUNTARY logouts).
    resetSessionState();
    wipeFailures();
    router.replace('/(auth)/login');
  };

  // M0 release/environment marker (Settings → Diagnostics). All values
  // come from existing Expo config — no extra native module:
  //  - version: app.json `expo.version`
  //  - build: ios.buildNumber / android.versionCode ('—' when unset,
  //    e.g. local dev before EAS assigns build numbers)
  //  - buildCommit: injected by app.config.ts from
  //    EAS_BUILD_GIT_COMMIT_HASH at EAS build time; 'dev' locally
  //  - apiUrl: the exact base URL the API client resolved at startup
  const appVersion = Constants.expoConfig?.version ?? '—';
  const iosBuild = Constants.expoConfig?.ios?.buildNumber;
  const androidBuild = Constants.expoConfig?.android?.versionCode;
  const build = iosBuild ?? (androidBuild != null ? String(androidBuild) : '—');
  const buildCommit =
    (Constants.expoConfig?.extra as { buildCommit?: string } | undefined)
      ?.buildCommit ?? 'dev';

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
      <View style={s.wrap}>
        <View style={s.headerRow}>
          <TouchableOpacity
            onPress={onBack}
            hitSlop={12}
            testID="settings-back"
            accessibilityRole="button"
            style={s.backBtn}
          >
            <Text style={s.backChevron}>{'‹'}</Text>
          </TouchableOpacity>
          <Text style={s.title}>{t('settings.title')}</Text>
          <View style={s.backSpacer} />
        </View>

        {/* Fidelity §13: account CARD — avatar initials + email + role
            chip. */}
        <View style={[s.card, s.accountCard]}>
          <View style={s.avatar}>
            <Text style={s.avatarText}>
              {(me?.email ?? '?').slice(0, 2).toUpperCase()}
            </Text>
          </View>
          <View style={s.accountMain}>
            <Text style={s.cardLabel}>{t('settings.signed_in_as')}</Text>
            {isLoading ? (
              <ActivityIndicator color={tokens.ink3} />
            ) : (
              <Text
                style={s.cardValue}
                numberOfLines={1}
                testID="settings-user-email"
              >
                {me?.email ?? '-'}
              </Text>
            )}
          </View>
          {isAdmin ? (
            <View style={s.roleChip}>
              <Text style={s.roleChipText}>{t('settings.admin')}</Text>
            </View>
          ) : null}
        </View>

        <Text style={s.groupLabel}>{t('settings.prefs')}</Text>
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

        {/* O3 (U5): in-app font size — the app does not follow the OS
            text-size setting, and field users need bigger text without
            digging through iOS settings. Applies instantly. */}
        <View style={s.card}>
          <Text style={s.cardLabel}>{t('settings.font_size')}</Text>
          <View style={s.langRow}>
            {(['standard', 'large', 'xlarge'] as FontScaleLevel[]).map(
              (level) => (
                <TouchableOpacity
                  key={level}
                  style={[s.langBtn, fontLevel === level && s.langBtnActive]}
                  onPress={() => void setFontLevel(level)}
                  testID={`settings-font-${level}`}
                  accessibilityRole="button"
                >
                  <Text
                    style={[
                      s.langBtnText,
                      fontLevel === level && s.langBtnTextActive,
                    ]}
                  >
                    {t(`settings.font_${level}`)}
                  </Text>
                </TouchableOpacity>
              ),
            )}
          </View>
        </View>

        {isAdmin ? (
          <Text style={s.groupLabel}>{t('settings.team')}</Text>
        ) : null}
        {isAdmin ? (
          <TouchableOpacity
            style={s.card}
            onPress={() => router.push('/users' as unknown as Href)}
            testID="settings-users-entry"
            accessibilityRole="button"
          >
            <View style={s.entryRow}>
              <Text style={s.cardValue}>{t('users.entry')}</Text>
              <Text style={s.entryChevron}>{'›'}</Text>
            </View>
          </TouchableOpacity>
        ) : null}

        {isAdmin ? (
          <Text style={s.groupLabel}>{t('settings.data')}</Text>
        ) : null}
        {isAdmin ? (
          <TouchableOpacity
            style={s.card}
            onPress={() => router.push('/export' as unknown as Href)}
            testID="settings-export-entry"
            accessibilityRole="button"
          >
            <View style={s.entryRow}>
              <Text style={s.cardValue}>{t('export.entry')}</Text>
              <Text style={s.entryChevron}>{'›'}</Text>
            </View>
          </TouchableOpacity>
        ) : null}

        <Text style={s.groupLabel}>{t('settings.about')}</Text>
        <View style={s.card} testID="settings-diagnostics">
          <Text style={s.cardLabel}>{t('settings.diagnostics')}</Text>
          <View style={s.diagRow}>
            <Text style={s.diagKey}>{t('settings.app_version')}</Text>
            <Text style={s.diagValue} testID="settings-app-version">
              {appVersion}
            </Text>
          </View>
          <View style={s.diagRow}>
            <Text style={s.diagKey}>{t('settings.build')}</Text>
            <Text style={s.diagValue} testID="settings-build">
              {build}
            </Text>
          </View>
          <View style={s.diagRow}>
            <Text style={s.diagKey}>{t('settings.build_commit')}</Text>
            <Text style={s.diagValue} testID="settings-build-commit">
              {buildCommit}
            </Text>
          </View>
          <View style={s.diagRow}>
            <Text style={s.diagKey}>{t('settings.api_host')}</Text>
            <Text
              style={[s.diagValue, s.diagValueLong]}
              numberOfLines={1}
              testID="settings-api-host"
            >
              {apiUrl}
            </Text>
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

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  wrap: { flex: 1, padding: 20 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 20 },
  backBtn: { minWidth: 44, minHeight: 44, justifyContent: 'center' },
  backChevron: { fontSize: 30, lineHeight: 32, color: tokens.ink2 },
  backSpacer: { width: 44 },
  title: { fontSize: 22, fontWeight: '800', color: tokens.ink },
  card: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    padding: 14,
    marginBottom: 12,
    backgroundColor: tokens.surface,
  },
  cardLabel: { color: tokens.ink3, fontSize: 13, marginBottom: 6 },
  accountCard: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  // Fidelity §13: group section labels (12/700 muted).
  groupLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: tokens.muted,
    marginBottom: 8,
    marginTop: 4,
  },
  avatar: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: tokens.sel,
    borderWidth: 1,
    borderColor: tokens.selBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: { fontSize: 15, fontWeight: '800', color: tokens.selText },
  accountMain: { flex: 1, minWidth: 0 },
  roleChip: {
    paddingHorizontal: 9,
    paddingVertical: 3,
    borderRadius: 999,
    backgroundColor: tokens.sel,
    borderWidth: 1,
    borderColor: tokens.selBorder,
  },
  roleChipText: { fontSize: 10.5, fontWeight: '700', color: tokens.selText },
  cardValue: { color: tokens.ink, fontSize: 16 },
  langRow: { flexDirection: 'row', gap: 8 },
  // F5: selection is TONAL (forey rule) — the dark-slate solid was the
  // pre-forey visual.
  langBtn: {
    flex: 1,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 10,
    alignItems: 'center',
    backgroundColor: tokens.surface,
  },
  langBtnActive: { backgroundColor: tokens.sel, borderColor: tokens.selBorder },
  langBtnText: { color: tokens.ink2, fontWeight: '500' },
  langBtnTextActive: { color: tokens.selText, fontWeight: '700' },
  entryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  entryChevron: { fontSize: 20, color: tokens.muted },
  diagRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 4,
  },
  diagKey: { color: tokens.ink2, fontSize: 14 },
  diagValue: { color: tokens.ink, fontSize: 14, fontVariant: ['tabular-nums'] },
  diagValueLong: { fontSize: 12, flexShrink: 1, marginLeft: 12 },
  // F5 (spec §13): red TONAL logout card — destructive, not alarming.
  logoutBtn: {
    marginTop: 12,
    padding: 14,
    borderRadius: 14,
    backgroundColor: tokens.badBg,
    borderWidth: 1,
    borderColor: tokens.badBorder,
    alignItems: 'center',
  },
  logoutText: { color: tokens.bad, fontWeight: '700', fontSize: 16 },
});
