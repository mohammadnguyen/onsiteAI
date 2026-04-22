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
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'expo-router';
import { api } from '../../src/api/client';
import { useAuthStore } from '../../src/store/auth';
import type { TokenPair } from '../../src/api/hooks/useAuth';

export default function Login() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const setTokens = useAuthStore((s) => s.setTokens);
  const router = useRouter();

  const onSubmit = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<TokenPair>('/auth/login', { email, password });
      await setTokens(r.data.access_token, r.data.refresh_token);
      router.replace('/(tabs)/jobs');
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
        <Text style={s.title}>{t('login.title')}</Text>
        <TextInput
          placeholder={t('login.email')}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          style={s.input}
          testID="login-email"
          accessibilityLabel={t('login.email')}
        />
        <TextInput
          placeholder={t('login.password')}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          style={s.input}
          testID="login-password"
          accessibilityLabel={t('login.password')}
        />
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
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  wrap: { flex: 1, justifyContent: 'center', padding: 24 },
  title: { fontSize: 28, fontWeight: '600', marginBottom: 32, textAlign: 'center', color: '#0f172a' },
  input: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    padding: 12,
    marginBottom: 12,
    fontSize: 16,
    backgroundColor: '#fff',
  },
  btn: { backgroundColor: '#1e293b', padding: 14, borderRadius: 6, alignItems: 'center' },
  btnDisabled: { opacity: 0.5 },
  btnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
  err: { color: '#b91c1c', marginBottom: 12, textAlign: 'center' },
});
