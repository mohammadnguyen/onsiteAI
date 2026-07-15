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
import { tokens } from '../ui/tokens';

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
 * O2-A (dogfood feedback #4): "Other" now defaults to a native calendar
 * picker — language-neutral, no typing — with the original TextInput
 * retained behind a "type instead" toggle. The typed AU short-date
 * shapes (``22/05``, ``22-05``, ``22/05/26`` etc.) remain a deliberate
 * operator fast path and are NOT removed; the backend's loose-date
 * parsing is untouched. On web (where the native picker module is
 * unavailable) the typed path stays the only custom mode, preserving
 * the pre-O2 behaviour exactly.
 */

// Native-only module, lazily required so the web bundle never
// EVALUATES the native picker (Metro may still include the file, but
// it only executes on iOS/Android). Web keeps the typed path.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let NativeDateTimePicker: any = null;
if (Platform.OS !== 'web') {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  NativeDateTimePicker = require('@react-native-community/datetimepicker').default;
}

type Mode = 'today' | 'yesterday' | 'custom';

/** How the "Other" date is being entered: native calendar (default on
 * iOS/Android) or the original typed TextInput (only option on web). */
type CustomEntry = 'picker' | 'typed';

export type DatePillsProps = {
  /** Current value as an ISO ``YYYY-MM-DD`` string. */
  value: string;
  /** Called with a fresh ISO ``YYYY-MM-DD`` whenever the user picks a valid date. */
  onChange: (iso: string) => void;
  /**
   * Optional validity callback fired whenever the input state's validity
   * changes. The component is considered:
   *   - VALID when mode is Today/Yesterday (always have a date), OR
   *   - VALID when mode is Custom AND the input parses cleanly (customError
   *     is null) AND the input is non-empty.
   *   - INVALID otherwise (custom mode with a parse error or with empty input).
   *
   * Used by the edit screen (PD-7=B) to hard-disable Save when the user is
   * looking at an invalid Other date, so a "last valid date" cannot be
   * silently committed while a wrong-looking string is on screen. The
   * Capture screen does NOT wire this callback — capture intentionally
   * keeps the "fall back to last valid" affordance for low-friction
   * one-handed entry.
   */
  onValidityChange?: (valid: boolean) => void;
  /** When true the pills + input are visually muted and not pressable. */
  disabled?: boolean;
};

export function DatePills({
  value,
  onChange,
  onValidityChange,
  disabled = false,
}: DatePillsProps) {
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
  // O2-A: calendar-first "Other" entry. Web has no native picker module,
  // so it stays on the typed path (pre-O2 behaviour).
  const [customEntry, setCustomEntry] = useState<CustomEntry>(
    Platform.OS === 'web' || !NativeDateTimePicker ? 'typed' : 'picker',
  );
  // Android's picker is a system dialog: rendering the component opens
  // it once. This flag controls that render; iOS shows an inline
  // calendar instead and never uses it.
  const [showAndroidPicker, setShowAndroidPicker] = useState(false);

  const customInputRef = useRef<TextInput>(null);

  // Validity model (see prop docs above). The edit screen (PD-7=B) gates
  // Save on this. Today/Yesterday are always valid. Custom in PICKER
  // entry is always valid — the calendar can only emit a real date, and
  // `value` always holds the last good ISO. Custom in TYPED entry is
  // valid only when there is text AND it parsed cleanly (unchanged).
  const isValid = useMemo<boolean>(() => {
    if (mode !== 'custom') return true;
    if (customEntry === 'picker' && NativeDateTimePicker) return true;
    if (customError !== null) return false;
    return customText.trim().length > 0;
  }, [mode, customEntry, customError, customText]);

  // Emit validity changes. Kept in a separate effect so consumers that
  // don't pass onValidityChange (e.g. the Capture screen) pay nothing.
  // The eslint-disable below is intentional: re-firing on `onValidityChange`
  // identity changes would create noise for parents that pass inline
  // closures.
  useEffect(() => {
    onValidityChange?.(isValid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isValid]);

  // Reflect external value changes (parent reset, or a records→labour
  // edit handoff seeding a past date) into our local mode + text
  // state. When the external value is neither Today nor Yesterday we
  // MUST flip into custom mode: before this else-branch existed
  // (audit X-1) the "Today" pill stayed lit while a past date was
  // actually loaded — the user saved to a day the pills lied about.
  useEffect(() => {
    if (value === today) {
      setMode('today');
      setCustomError(null);
    } else if (value === yesterday) {
      setMode('yesterday');
      setCustomError(null);
    } else {
      setMode('custom');
      setCustomText(formatDateAU(value));
      setCustomError(null);
    }
    // Deps are value + rollover only — this effect syncs EXTERNAL
    // changes; pill taps set mode directly in their handlers.
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
    setCustomError(null);
    if (customEntry === 'picker' && NativeDateTimePicker) {
      // Calendar-first: on Android pop the system dialog immediately;
      // on iOS the inline calendar renders below the pills.
      if (Platform.OS === 'android') setShowAndroidPicker(true);
      return;
    }
    // Typed entry (web, or user preference): seed the input with the
    // current value so the user can edit rather than retype.
    if (customText.trim().length === 0) {
      setCustomText(formatDateAU(value));
    }
    setTimeout(() => customInputRef.current?.focus(), 0);
  };

  // O2-A: picker emission. The native calendar can only produce a real
  // date; normalize to ISO and emit. Android also closes its dialog
  // (dismiss = keep the last good value, emit nothing).
  const onPickerChange = (_event: unknown, picked?: Date) => {
    if (Platform.OS === 'android') setShowAndroidPicker(false);
    if (picked) onChange(dateToISO(picked));
  };

  const switchToTyped = () => {
    if (disabled) return;
    setCustomEntry('typed');
    setShowAndroidPicker(false);
    if (customText.trim().length === 0) {
      setCustomText(formatDateAU(value));
    }
    setCustomError(null);
    setTimeout(() => customInputRef.current?.focus(), 0);
  };

  const switchToPicker = () => {
    if (disabled || !NativeDateTimePicker) return;
    setCustomEntry('picker');
    setCustomError(null);
    if (Platform.OS === 'android') setShowAndroidPicker(true);
  };

  // Date object for the native picker: `value` always holds the last
  // good ISO, so this parse cannot fail in practice; today is the
  // defensive fallback.
  const pickerDate = parseLooseDate(value) ?? new Date();

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
      {mode === 'custom' && customEntry === 'picker' && NativeDateTimePicker ? (
        <View style={s.customWrap}>
          {Platform.OS === 'android' ? (
            <TouchableOpacity
              onPress={() => !disabled && setShowAndroidPicker(true)}
              disabled={disabled}
              style={s.pickerValueRow}
              testID="date-picker-open"
              accessibilityRole="button"
              accessibilityLabel={t('capture.date_custom')}
            >
              <Text style={s.pickerValueText}>{formatDateAU(value)}</Text>
            </TouchableOpacity>
          ) : null}
          {Platform.OS === 'ios' || showAndroidPicker ? (
            <NativeDateTimePicker
              value={pickerDate}
              mode="date"
              display={Platform.OS === 'ios' ? 'inline' : 'default'}
              onChange={onPickerChange}
              themeVariant="light"
              testID="date-picker-native"
            />
          ) : null}
          <TouchableOpacity
            onPress={switchToTyped}
            disabled={disabled}
            testID="date-type-instead"
            accessibilityRole="button"
          >
            <Text style={s.entryToggleText}>
              {t('capture.date_type_instead')}
            </Text>
          </TouchableOpacity>
        </View>
      ) : null}
      {mode === 'custom' && (customEntry === 'typed' || !NativeDateTimePicker) ? (
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
          {NativeDateTimePicker ? (
            <TouchableOpacity
              onPress={switchToPicker}
              disabled={disabled}
              testID="date-use-calendar"
              accessibilityRole="button"
            >
              <Text style={s.entryToggleText}>
                {t('capture.date_use_calendar')}
              </Text>
            </TouchableOpacity>
          ) : null}
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
      // B4.5 review: dim the LABEL, never the tonal fill (an opacity
      // pass over sel-blue washes the selected pill out to white).
      hitSlop={{ top: 6, bottom: 6, left: 4, right: 4 }}
      style={[s.pill, active && s.pillActive]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text
        style={[
          s.pillText,
          active && s.pillTextActive,
          disabled && s.pillTextDisabled,
        ]}
      >
        {label}
      </Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 8 },
  label: { color: '#475569', fontSize: 14 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  // UI-kit v2 (B4.5): tonal selected state per the design spec —
  // solid blue is reserved for ACTIONS, never for selection.
  // Metrics match the kit Chip exactly (14/6/12.5) so the capture
  // screen renders ONE chip family across quick-job, date and payment
  // rows (design ③).
  pill: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 999,
    backgroundColor: '#ffffff',
  },
  pillActive: { backgroundColor: tokens.sel, borderColor: tokens.selBorder },
  pillText: { color: tokens.ink2, fontSize: 12.5, fontWeight: '500' },
  pillTextActive: { color: tokens.selText, fontWeight: '600' },
  pillTextDisabled: { color: tokens.ink3 },
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
  // O2-A calendar entry styles.
  pickerValueRow: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#ffffff',
  },
  pickerValueText: { fontSize: 16, color: '#0f172a' },
  entryToggleText: { color: '#2563eb', fontSize: 13, fontWeight: '500' },
});
