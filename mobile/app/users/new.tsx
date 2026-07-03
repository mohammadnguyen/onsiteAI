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
  ScrollView,
  Keyboard,
  Pressable,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useInviteUser,
  type UserInviteInput,
  type UserRole,
} from '../../src/api/hooks/useUsers';
import { useOneShotBack } from '../../src/util/navigation';

/**
 * M4: create-user form (admin-only).
 *
 * Route: ``/users/new``, pushed from the Users list header.
 *
 * Implements the backend's Phase 1 invite design verbatim: the admin
 * types an initial password here and tells the new user out of band —
 * no email is sent, and there is NO self-service reset yet (known v1
 * limitation, operator-accepted: a forgotten password has no recovery
 * path until the Phase-6 backend work).
 *
 * Password handling: secureTextEntry with a show/hide toggle so the
 * admin can read the password back to the new user; the value lives
 * only in component state, goes out once over HTTPS in the request
 * body, and is never logged or persisted client-side.
 *
 * 409s (duplicate email, admin cap) surface the backend detail
 * verbatim in the inline error banner.
 */

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d: { msg?: string }) => d.msg ?? '')
        .filter(Boolean)
        .join('; ');
    }
    if (error.message) return error.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export default function NewUserScreen() {
  const { t } = useTranslation();
  const invite = useInviteUser();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<UserRole>('contributor');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [language, setLanguage] = useState<'en' | 'zh'>('en');
  const [formError, setFormError] = useState<string | null>(null);

  const onBack = useOneShotBack('/(tabs)/settings');

  // Light client-side gate only — the backend validates properly
  // (EmailStr etc.). Password is NOT trimmed: leading/trailing
  // characters the admin typed are part of the password.
  const submitDisabled =
    invite.isPending ||
    fullName.trim().length === 0 ||
    email.trim().length === 0 ||
    !email.includes('@') ||
    password.length === 0;

  const onSubmit = async () => {
    if (submitDisabled) return;
    setFormError(null);
    Keyboard.dismiss();
    const body: UserInviteInput = {
      full_name: fullName.trim(),
      email: email.trim(),
      role,
      initial_password: password,
      language_preference: language,
    };
    try {
      await invite.mutateAsync(body);
      onBack();
    } catch (err) {
      setFormError(extractErrorMessage(err, t('users.create_error')));
    }
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="new-user-back"
          accessibilityRole="button"
          accessibilityLabel={t('common.cancel')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('common.cancel')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('users.create_title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      <KeyboardAvoidingView
        style={s.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          testID="new-user-form"
        >
          <Text style={s.label}>{t('users.field_name')}</Text>
          <TextInput
            value={fullName}
            onChangeText={setFullName}
            placeholderTextColor="#94a3b8"
            editable={!invite.isPending}
            style={s.input}
            testID="new-user-name"
            accessibilityLabel={t('users.field_name')}
          />

          <Text style={s.label}>{t('users.field_email')}</Text>
          <TextInput
            value={email}
            onChangeText={setEmail}
            placeholderTextColor="#94a3b8"
            editable={!invite.isPending}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            style={s.input}
            testID="new-user-email"
            accessibilityLabel={t('users.field_email')}
          />

          <View style={s.radioRow}>
            <Text style={s.radioLabel}>{t('users.field_role')}</Text>
            <RadioOption
              label={t('users.role_contributor')}
              active={role === 'contributor'}
              disabled={invite.isPending}
              onPress={() => setRole('contributor')}
              testID="new-user-role-contributor"
            />
            <RadioOption
              label={t('users.role_admin')}
              active={role === 'admin'}
              disabled={invite.isPending}
              onPress={() => setRole('admin')}
              testID="new-user-role-admin"
            />
          </View>

          <View style={s.radioRow}>
            <Text style={s.radioLabel}>{t('users.field_language')}</Text>
            <RadioOption
              label={t('settings.english')}
              active={language === 'en'}
              disabled={invite.isPending}
              onPress={() => setLanguage('en')}
              testID="new-user-lang-en"
            />
            <RadioOption
              label={t('settings.chinese')}
              active={language === 'zh'}
              disabled={invite.isPending}
              onPress={() => setLanguage('zh')}
              testID="new-user-lang-zh"
            />
          </View>

          <Text style={s.label}>{t('users.field_password')}</Text>
          <View style={s.passwordRow}>
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholderTextColor="#94a3b8"
              editable={!invite.isPending}
              secureTextEntry={!showPassword}
              autoCapitalize="none"
              autoCorrect={false}
              style={[s.input, s.passwordInput]}
              testID="new-user-password"
              accessibilityLabel={t('users.field_password')}
            />
            <Pressable
              onPress={() => setShowPassword((v) => !v)}
              hitSlop={8}
              testID="new-user-password-toggle"
              accessibilityRole="button"
              style={({ pressed }) => [s.toggleBtn, pressed && s.backBtnPressed]}
            >
              <Text style={s.toggleText}>
                {t(showPassword ? 'users.hide_password' : 'users.show_password')}
              </Text>
            </Pressable>
          </View>
          <Text style={s.hint}>{t('users.password_hint')}</Text>

          {formError ? (
            <View style={s.errorBanner} testID="new-user-error">
              <Text style={s.errorBannerText}>{formError}</Text>
            </View>
          ) : null}

          <TouchableOpacity
            onPress={onSubmit}
            disabled={submitDisabled}
            style={[s.submitBtn, submitDisabled && s.submitBtnDisabled]}
            testID="new-user-submit"
            accessibilityRole="button"
            accessibilityState={{ disabled: submitDisabled }}
          >
            {invite.isPending ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.submitBtnText}>{t('users.create_cta')}</Text>
            )}
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function RadioOption({
  label,
  active,
  disabled,
  onPress,
  testID,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        s.radioOption,
        active && s.radioOptionActive,
        disabled && s.radioOptionDisabled,
      ]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text style={[s.radioOptionText, active && s.radioOptionTextActive]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  backBtnPressed: { opacity: 0.5 },
  backChevron: {
    fontSize: 28,
    color: '#1e293b',
    marginRight: 4,
    lineHeight: 28,
  },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { width: 72 },
  scroll: { padding: 16, gap: 14 },
  label: { color: '#475569', fontSize: 14, marginTop: 4 },
  input: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  radioRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  radioLabel: { color: '#475569', fontSize: 14, marginRight: 4 },
  radioOption: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  radioOptionActive: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  radioOptionDisabled: { opacity: 0.5 },
  radioOptionText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  radioOptionTextActive: { color: '#ffffff' },
  passwordRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  passwordInput: { flex: 1 },
  toggleBtn: { paddingHorizontal: 10, paddingVertical: 10 },
  toggleText: { color: '#1e293b', fontSize: 14, fontWeight: '600' },
  hint: { color: '#64748b', fontSize: 12 },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 12,
  },
  errorBannerText: { color: '#991b1b', fontSize: 14 },
  submitBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
    marginTop: 8,
  },
  submitBtnDisabled: { opacity: 0.4 },
  submitBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
