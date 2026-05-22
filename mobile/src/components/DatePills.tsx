import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Platform,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import {
  dateToISO,
  formatDateAU,
  parseLooseDate,
  todayISO,
  yesterdayISO,
} from '../util/dates';

/**
 * Mobile expense-date pills.
 *
 * Three buttons: Today (default), Yesterday, Other. "Other" reveals an
 * inline text input that accepts the AU short-date shapes the backend
 * parser handles (``DD/MM``, ``D/M``, ``DD-MM``, ``DD.MM``,
 * ``DD/MM/YY``, ``DD/MM/YYYY``) plus the canonical ISO
 * ``YYYY-MM-DD``. On a valid input the parsed date is normalized to
 * ISO and emitted via ``onChange``. On an invalid input an inline
 * error renders and ``onChange`` is NOT called — the parent's
 * ``value`` stays at the last good ISO, so the submit body always
 * carries a parseable date.
 *
 * Backend parser remains the source of truth: this component is
 * UX-only. Submitting still routes through ``POST /expenses``, which
 * re-normalizes any string via the schema's ``ExpenseDateField``
 * BeforeValidator.
 *
 * Pinned to a TextInput rather than a native date picker because:
 *  - the AU short-date shapes the operator specified
 *    (``22/05``, ``22-05``, ``22/05/26`` etc.) are typed inputs the
 *    native picker doesn't surface,
 *  - no ``@react-native-community/datetimepicker`` dependency churn,
 *  - identical behaviour across iOS / Android / web.
 */

type Mode = 'today' | 'yesterday' | 'custom';

export type DatePillsProps = {
  /** Current value as an ISO ``YYYY-MM-DD`` string. */
  value: string;
  /** Called with a fresh ISO ``YYYY-MM-DD`` whenever the user picks a valid date. */
  onChange: (iso: string) => void;
  /** When true the pills + input are visually muted and not pressable. */
  disabled?: boolean;
};

export function DatePills({ value, onChange, disabled = false }: DatePillsProps) {
  const { t } = useTranslation();

  const today = todayISO();
  const yesterday = yesterdayISO();

  const initialMode: Mode = useMemo(() => {
    if (value === today) return 'today';
    if (value === yesterday) return 'yesterday';
    return 'custom';
  }, [value, today, yesterday]);

  const [mode, setMode] = useState<Mode>(initialMode);
  const [customText, setCustomText] = useState<string>(() =>
    initialMode === 'custom' ? formatDateAU(value) : '',
  );
  const [customError, setCustomError] = useState<string | null>(null);

  const customInputRef = useRef<TextInput>(null);

  // Reflect external value changes (e.g. parent reset) into our local
  // mode + text state. We deliberately do NOT auto-flip into custom on
  // an external change; only sync the displayed text when we're
  // already in custom mode.
  useEffect(() => {
    if (value === today) {
      setMode('today');
      setCustomError(null);
    } else if (value === yesterday) {
      setMode('yesterday');
      setCustomError(null);
    } else if (mode === 'custom') {
      setCustomText(formatDateAU(value));
      setCustomError(null);
    }
    // `mode` is intentionally excluded — this effect should only run
    // when the value (or today/yesterday rollover) changes externally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, today, yesterday]);

  const selectToday = () => {
    if (disabled) return;
    setMode('today');
    setCustomError(null);
    setCustomText('');
    onChange(today);
  };

  const selectYesterday = () => {
    if (disabled) return;
    setMode('yesterday');
    setCustomError(null);
    setCustomText('');
    onChange(yesterday);
  };

  const selectCustom = () => {
    if (disabled) return;
    setMode('custom');
    // Seed the input with the current value so the user can edit
    // rather than retype from scratch.
    if (customText.trim().length === 0) {
      setCustomText(formatDateAU(value));
    }
    setCustomError(null);
    setTimeout(() => customInputRef.current?.focus(), 0);
  };

  const onCustomChange = (text: string) => {
    setCustomText(text);
    if (text.trim().length === 0) {
      // Empty input is neither valid nor an error — just no-op until
      // the user types something. We don't propagate; parent keeps
      // the last good value.
      setCustomError(null);
      return;
    }
    const parsed = parseLooseDate(text);
    if (parsed) {
      setCustomError(null);
      onChange(dateToISO(parsed));
    } else {
      setCustomError(t('capture.date_invalid'));
    }
  };

  return (
    <View style={s.wrap}>
      <Text style={s.label}>{t('capture.date_label')}</Text>
      <View style={s.row}>
        <Pill
          label={t('capture.date_today')}
          active={mode === 'today'}
          disabled={disabled}
          onPress={selectToday}
          testID="date-today"
        />
        <Pill
          label={t('capture.date_yesterday')}
          active={mode === 'yesterday'}
          disabled={disabled}
          onPress={selectYesterday}
          testID="date-yesterday"
        />
        <Pill
          label={t('capture.date_custom')}
          active={mode === 'custom'}
          disabled={disabled}
          onPress={selectCustom}
          testID="date-custom"
        />
      </View>
      {mode === 'custom' ? (
        <View style={s.customWrap}>
          <TextInput
            ref={customInputRef}
            value={customText}
            onChangeText={onCustomChange}
            placeholder={t('capture.date_custom_placeholder')}
            placeholderTextColor="#94a3b8"
            editable={!disabled}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType={
              Platform.OS === 'ios' ? 'numbers-and-punctuation' : 'default'
            }
            style={[s.customInput, customError ? s.customInputError : null]}
            testID="date-custom-input"
            accessibilityLabel={t('capture.date_custom')}
          />
          {customError ? (
            <Text style={s.errorText} testID="date-custom-error">
              {customError}
            </Text>
          ) : (
            <Text style={s.hintText}>{t('capture.date_custom_hint')}</Text>
          )}
        </View>
      ) : null}
    </View>
  );
}

function Pill({
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
      style={[s.pill, active && s.pillActive, disabled && s.pillDisabled]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text style={[s.pillText, active && s.pillTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 8 },
  label: { color: '#475569', fontSize: 14 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  pillActive: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  pillDisabled: { opacity: 0.5 },
  pillText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  pillTextActive: { color: '#ffffff' },
  customWrap: { gap: 4 },
  customInput: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  customInputError: { borderColor: '#dc2626' },
  hintText: { color: '#64748b', fontSize: 12 },
  errorText: { color: '#b91c1c', fontSize: 13 },
});
