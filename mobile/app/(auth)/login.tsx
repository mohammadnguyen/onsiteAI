import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useRouter, type Href } from 'expo-router';
import { api } from '../../src/api/client';
import { useAuthStore } from '../../src/store/auth';
import { setLanguage } from '../../src/i18n';
import type { TokenPair } from '../../src/api/hooks/useAuth';
import { ForeyLogo, MailIcon, LockIcon, FaceIdIcon } from '../../src/ui/icons';
import { tokens } from '../../src/ui/tokens';

/**
 * forey F0: branded login (handoff §1). Brand block (logo 76 +
 * lowercase wordmark + tagline), white 52-high inputs with leading
 * icons, solid-primary CTA, language segment at the bottom (instant
 * i18next switch — same mechanism as Settings).
 *
 * Deliberately NOT built from the prototype: the Face ID button (no
 * local-auth implementation or dependency in the app) and "忘记密码"
 * (no password-reset endpoint on the backend). Honest UI only — both
 * need their own feature slices.
 */
export default function Login() {
  const { t, i18n } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const setTokens = useAuthStore((s) => s.setTokens);
  const router = useRouter();
  const lang = i18n.language?.startsWith('zh') ? 'zh' : 'en';

  const onSubmit = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<TokenPair>('/auth/login', { email, password });
      await setTokens(r.data.access_token, r.data.refresh_token);
      router.replace('/(tabs)/home' as unknown as Href);
    } catch (e: unknown) {
      const errObj = e as { response?: { data?: { detail?: string } } };
      setErr(errObj?.response?.data?.detail ?? t('login.error'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={s.safe}>
      <KeyboardAvoidingView
        style={s.wrap}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={s.brand}>
          <ForeyLogo size={76} />
          <Text style={s.wordmark}>forey</Text>
          <Text style={s.tagline}>{t('login.tagline')}</Text>
        </View>

        <View style={s.inputRow}>
          <MailIcon size={18} color={tokens.muted} />
          <TextInput
            placeholder={t('login.email')}
            placeholderTextColor={tokens.ink3}
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoComplete="email"
            keyboardType="email-address"
            style={s.input}
            testID="login-email"
            accessibilityLabel={t('login.email')}
          />
        </View>
        <View style={s.inputRow}>
          <LockIcon size={18} color={tokens.muted} />
          <TextInput
            placeholder={t('login.password')}
            placeholderTextColor={tokens.ink3}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            style={s.input}
            testID="login-password"
            accessibilityLabel={t('login.password')}
          />
        </View>
        {err ? <Text style={s.err}>{err}</Text> : null}
        <TouchableOpacity
          onPress={onSubmit}
          disabled={busy}
          style={[s.btn, busy && s.btnDisabled]}
          testID="login-submit"
          accessibilityRole="button"
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={s.btnText}>{t('login.submit')}</Text>
          )}
        </TouchableOpacity>

        {/* Spec §1: Face ID + forgot password render per design.
            Neither has a backing implementation yet — tapping says so
            honestly (operator-authorised coming-soon pattern, same as
            the mic). */}
        <TouchableOpacity
          style={s.faceIdBtn}
          onPress={() => Alert.alert(t('settings.coming_soon'))}
          accessibilityRole="button"
          testID="login-faceid"
        >
          <FaceIdIcon size={19} color={tokens.primary} />
          <Text style={s.faceIdText}>{t('login.face_id')}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={s.forgotBtn}
          onPress={() => Alert.alert(t('settings.coming_soon'))}
          accessibilityRole="button"
          testID="login-forgot"
        >
          <Text style={s.forgotText}>{t('login.forgot')}</Text>
        </TouchableOpacity>

        <View style={s.langWrap}>
          <View style={s.langSeg}>
            <TouchableOpacity
              onPress={() => void setLanguage('zh')}
              style={[s.langOpt, lang === 'zh' && s.langOptOn]}
              accessibilityRole="radio"
              accessibilityState={{ selected: lang === 'zh' }}
              testID="login-lang-zh"
            >
              <Text style={[s.langText, lang === 'zh' && s.langTextOn]}>
                中文
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => void setLanguage('en')}
              style={[s.langOpt, lang === 'en' && s.langOptOn]}
              accessibilityRole="radio"
              accessibilityState={{ selected: lang === 'en' }}
              testID="login-lang-en"
            >
              <Text style={[s.langText, lang === 'en' && s.langTextOn]}>
                EN
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  wrap: { flex: 1, justifyContent: 'center', padding: 24 },
  brand: { alignItems: 'center', marginBottom: 36 },
  // Spec: SF Pro Rounded unavailable in RN without bundling the font
  // file — System + 800 approximation per the handoff's own fallback.
  wordmark: {
    fontSize: 36,
    fontWeight: '800',
    color: tokens.ink,
    letterSpacing: -1,
    marginTop: 14,
  },
  tagline: { fontSize: 13, color: tokens.ink3, marginTop: 4 },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    paddingHorizontal: 14,
    height: 52,
    marginBottom: 12,
  },
  input: { flex: 1, fontSize: 16, color: tokens.ink },
  btn: {
    backgroundColor: tokens.primary,
    height: 52,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 4,
    shadowColor: tokens.primary,
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.32,
    shadowRadius: 22,
    elevation: 6,
  },
  btnDisabled: { opacity: 0.5 },
  btnText: { color: '#ffffff', fontWeight: '700', fontSize: 16 },
  err: { color: tokens.bad, marginBottom: 12, textAlign: 'center' },
  faceIdBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    height: 52,
    borderRadius: 14,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    marginTop: 12,
  },
  faceIdText: { fontSize: 15, fontWeight: '700', color: tokens.primary },
  forgotBtn: { alignItems: 'center', marginTop: 14 },
  forgotText: { fontSize: 13, fontWeight: '600', color: tokens.ink3 },
  langWrap: { alignItems: 'center', marginTop: 26 },
  langSeg: {
    flexDirection: 'row',
    backgroundColor: tokens.segTrack,
    borderRadius: 999,
    padding: 3,
    gap: 2,
  },
  langOpt: {
    paddingHorizontal: 18,
    paddingVertical: 7,
    borderRadius: 999,
  },
  langOptOn: {
    backgroundColor: tokens.surface,
    shadowColor: tokens.ink,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.12,
    shadowRadius: 3,
    elevation: 2,
  },
  langText: { fontSize: 13, fontWeight: '600', color: tokens.ink3 },
  langTextOn: { color: tokens.ink },
});
