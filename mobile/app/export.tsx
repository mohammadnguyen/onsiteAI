import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import * as Sharing from 'expo-sharing';

import { useMe } from '../src/api/hooks/useAuth';
import { parseLooseDate, dateToISO, formatDateAU } from '../src/util/dates';
import { useOneShotBack } from '../src/util/navigation';

// O3: native calendar assist for the From/To fields. Lazily required so
// the web export never evaluates the native module (same pattern as
// DatePills). Typed entry stays the primary path — the picker only
// FILLS the text field.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let NativeDateTimePicker: any = null;
if (Platform.OS !== 'web') {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  NativeDateTimePicker = require('@react-native-community/datetimepicker').default;
}
import {
  downloadExpensesExcel,
  ExportError,
  type ExportErrorKind,
} from '../src/api/reports';

/**
 * A4: accountant Excel export (admin-only).
 *
 * Route: ``/export``. Entered from the admin-only Settings entry.
 *
 * Reuses the existing backend ``GET /reports/expenses-excel`` (admin-only,
 * reviewed-only by default). v1 controls: optional From / To dates and an
 * Include-pending toggle (default OFF). No job filter in v1.
 *
 * Download + share live in ``src/api/reports.ts``; this screen is input,
 * validation and error presentation only. Non-admins never see the entry,
 * and the backend 403s anyway — the admin guard here is defence in depth
 * (deep-link) and fails closed while the role loads.
 */

function errorKey(kind: ExportErrorKind): string {
  switch (kind) {
    case 'session_expired':
      return 'export.error_session';
    case 'forbidden':
      return 'export.error_forbidden';
    case 'bad_dates':
      return 'export.error_dates';
    case 'timeout':
      return 'export.error_timeout';
    case 'network':
      return 'export.error_network';
    default:
      return 'export.error_generic';
  }
}

export default function ExportScreen() {
  const { t } = useTranslation();
  const { data: me, isLoading: meLoading } = useMe();
  const isAdmin = me?.role === 'admin';

  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [includePending, setIncludePending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fromError, setFromError] = useState(false);
  const [toError, setToError] = useState(false);
  // O3: which field (if any) the native calendar is currently filling.
  const [pickerFor, setPickerFor] = useState<'from' | 'to' | null>(null);

  const onPicked = (_event: unknown, picked?: Date) => {
    const target = pickerFor;
    setPickerFor(null);
    if (!picked || !target) return;
    const text = formatDateAU(dateToISO(picked));
    if (target === 'from') {
      setFrom(text);
      setFromError(false);
    } else {
      setTo(text);
      setToError(false);
    }
  };

  const onBack = useOneShotBack('/(tabs)/settings');

  const onExport = async () => {
    if (busy) return;
    setError(null);
    setFromError(false);
    setToError(false);

    // Mobile-side date parsing is UX-only — the backend remains the source
    // of truth. We canonicalize to ISO and pre-check the range so an
    // obvious mistake is caught before the network round-trip.
    let fromISO: string | undefined;
    let toISO: string | undefined;

    const fromRaw = from.trim();
    if (fromRaw) {
      const d = parseLooseDate(fromRaw);
      if (!d) {
        setFromError(true);
        setError(t('export.date_invalid'));
        return;
      }
      fromISO = dateToISO(d);
    }

    const toRaw = to.trim();
    if (toRaw) {
      const d = parseLooseDate(toRaw);
      if (!d) {
        setToError(true);
        setError(t('export.date_invalid'));
        return;
      }
      toISO = dateToISO(d);
    }

    // ISO YYYY-MM-DD compares correctly as strings.
    if (fromISO && toISO && fromISO > toISO) {
      setError(t('export.date_range_invalid'));
      return;
    }

    setBusy(true);
    try {
      // No point downloading if the device can't present a share sheet.
      const available = await Sharing.isAvailableAsync();
      if (!available) {
        setError(t('export.error_sharing_unavailable'));
        return;
      }
      const result = await downloadExpensesExcel({
        fromDate: fromISO,
        toDate: toISO,
        includePending,
      });
      // A 200 with no matching rows is still a valid workbook — sharing an
      // empty/headers-only sheet is the documented success path for v1.
      await Sharing.shareAsync(result.uri, {
        mimeType: result.mimeType,
        UTI: result.uti,
        dialogTitle: t('export.share_dialog_title'),
      });
    } catch (err) {
      const kind = err instanceof ExportError ? err.kind : 'generic';
      setError(t(errorKey(kind)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="export-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('export.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {meLoading ? (
        <View style={s.state} testID="export-loading">
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : !isAdmin ? (
        <View style={s.state} testID="export-forbidden">
          <Text style={s.stateText}>{t('export.forbidden')}</Text>
        </View>
      ) : (
        <ScrollView
          style={s.body}
          contentContainerStyle={s.bodyContent}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={s.hint}>{t('export.hint')}</Text>

          <Text style={s.label}>{t('export.from_label')}</Text>
          <TextInput
            value={from}
            onChangeText={setFrom}
            editable={!busy}
            placeholder={t('export.date_placeholder')}
            placeholderTextColor="#94a3b8"
            style={[s.input, fromError && s.inputError]}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="numbers-and-punctuation"
            testID="export-from"
          />
          {NativeDateTimePicker ? (
            <Pressable
              onPress={() => !busy && setPickerFor('from')}
              testID="export-from-calendar"
              accessibilityRole="button"
            >
              <Text style={s.calendarLink}>
                {t('capture.date_use_calendar')}
              </Text>
            </Pressable>
          ) : null}

          <Text style={s.label}>{t('export.to_label')}</Text>
          <TextInput
            value={to}
            onChangeText={setTo}
            editable={!busy}
            placeholder={t('export.date_placeholder')}
            placeholderTextColor="#94a3b8"
            style={[s.input, toError && s.inputError]}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="numbers-and-punctuation"
            testID="export-to"
          />
          {NativeDateTimePicker ? (
            <Pressable
              onPress={() => !busy && setPickerFor('to')}
              testID="export-to-calendar"
              accessibilityRole="button"
            >
              <Text style={s.calendarLink}>
                {t('capture.date_use_calendar')}
              </Text>
            </Pressable>
          ) : null}
          {pickerFor && NativeDateTimePicker ? (
            <NativeDateTimePicker
              value={
                parseLooseDate(pickerFor === 'from' ? from : to) ?? new Date()
              }
              mode="date"
              display={Platform.OS === 'ios' ? 'inline' : 'default'}
              onChange={onPicked}
              themeVariant="light"
              testID="export-date-picker"
            />
          ) : null}
          <Text style={s.fieldHint}>{t('export.date_hint')}</Text>

          <View style={s.toggleRow}>
            <View style={s.toggleText}>
              <Text style={s.toggleLabel}>{t('export.include_pending')}</Text>
              <Text style={s.fieldHint}>
                {t('export.include_pending_hint')}
              </Text>
            </View>
            <Switch
              value={includePending}
              onValueChange={setIncludePending}
              disabled={busy}
              testID="export-include-pending"
            />
          </View>

          {error ? (
            <Text style={s.error} testID="export-error">
              {error}
            </Text>
          ) : null}

          <Pressable
            onPress={() => void onExport()}
            disabled={busy}
            style={({ pressed }) => [
              s.cta,
              busy && s.disabled,
              pressed && s.pressed,
            ]}
            testID="export-cta"
            accessibilityRole="button"
          >
            {busy ? (
              <View style={s.ctaBusy}>
                <ActivityIndicator color="#ffffff" />
                <Text style={s.ctaText}>{t('export.exporting')}</Text>
              </View>
            ) : (
              <Text style={s.ctaText}>{t('export.cta')}</Text>
            )}
          </Pressable>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
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
  backChevron: { fontSize: 28, color: '#1e293b', marginRight: 4, lineHeight: 28 },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { width: 72 },
  body: { flex: 1 },
  bodyContent: { padding: 20 },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  hint: { fontSize: 14, color: '#475569', marginBottom: 20, lineHeight: 20 },
  label: { fontSize: 13, color: '#64748b', marginBottom: 6 },
  input: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    backgroundColor: '#ffffff',
    marginBottom: 14,
  },
  inputError: { borderColor: '#b91c1c' },
  fieldHint: { fontSize: 12, color: '#94a3b8', marginTop: -6, marginBottom: 16 },
  // O3: calendar-assist link under each date field.
  calendarLink: { fontSize: 13, color: '#2563eb', fontWeight: '500', marginBottom: 12 },
  toggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginTop: 4,
    marginBottom: 8,
  },
  toggleText: { flex: 1 },
  toggleLabel: { fontSize: 15, color: '#0f172a', marginBottom: 2 },
  error: { color: '#b45309', fontSize: 14, marginTop: 12, marginBottom: 4 },
  cta: {
    marginTop: 20,
    backgroundColor: '#15803d',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
  },
  ctaBusy: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  ctaText: { color: '#ffffff', fontWeight: '700', fontSize: 16 },
  disabled: { opacity: 0.6 },
  pressed: { opacity: 0.8 },
});
