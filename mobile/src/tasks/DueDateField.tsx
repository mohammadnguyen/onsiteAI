import { useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { dateToISO, formatDateAU, parseLooseDate } from '../util/dates';
import { tokens } from '../ui/tokens';

// The native calendar, required lazily so the web build never evaluates the
// native module - the same pattern as DatePills and the export screen.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let NativeDateTimePicker: any = null;
if (Platform.OS !== 'web') {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  NativeDateTimePicker = require('@react-native-community/datetimepicker').default;
}

function isoToDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** Typed text that can become the due date: nothing, or a real date. */
export function isTypedDueValid(text: string): boolean {
  return !text.trim() || parseLooseDate(text) !== null;
}

/**
 * An optional due date: a calendar date, no time (YYYY-MM-DD on the wire).
 * Picked from the native calendar; typed as DD/MM/YYYY where there is none.
 * Clearable.
 *
 * Typed text becomes the value when the field is left. Until then, a form
 * that saves must not: `onValidityChange` says whether the text could
 * become a date, so a mistyped one is never silently replaced by the old
 * value.
 */
export function DueDateField({
  value,
  disabled,
  onChange,
  onValidityChange,
}: {
  value: string | null;
  disabled: boolean;
  onChange: (iso: string | null) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);
  const [typed, setTyped] = useState('');
  const [typedError, setTypedError] = useState(false);

  return (
    <View style={s.wrap}>
      <View style={s.row}>
        <Text style={s.value} testID="due-date-value">
          {value ? formatDateAU(value) : t('tasks.due.none')}
        </Text>
        {NativeDateTimePicker ? (
          <Pressable
            onPress={() => !disabled && setPicking((p) => !p)}
            disabled={disabled}
            accessibilityRole="button"
            hitSlop={8}
            testID="due-date-pick"
          >
            <Text style={[s.link, disabled ? s.dim : null]}>{t('tasks.due.pick')}</Text>
          </Pressable>
        ) : null}
        {value ? (
          <Pressable
            onPress={() => !disabled && onChange(null)}
            disabled={disabled}
            accessibilityRole="button"
            hitSlop={8}
            testID="due-date-clear"
          >
            <Text style={[s.clear, disabled ? s.dim : null]}>{t('tasks.due.clear')}</Text>
          </Pressable>
        ) : null}
      </View>
      {picking && NativeDateTimePicker ? (
        <NativeDateTimePicker
          value={value ? isoToDate(value) : new Date()}
          mode="date"
          display={Platform.OS === 'ios' ? 'inline' : 'default'}
          themeVariant="light"
          onChange={(event: { type?: string } | undefined, picked?: Date) => {
            setPicking(false);
            // Android reports a dismissed dialog with the old value: not a choice.
            if (event?.type === 'dismissed') return;
            if (picked) onChange(dateToISO(picked));
          }}
          testID="due-date-picker"
        />
      ) : null}
      {!NativeDateTimePicker ? (
        <TextInput
          value={typed}
          editable={!disabled}
          onChangeText={(text) => {
            setTyped(text);
            setTypedError(false);
            onValidityChange?.(isTypedDueValid(text));
          }}
          onBlur={() => {
            if (!typed.trim()) return;
            const parsed = parseLooseDate(typed);
            if (parsed) {
              onChange(dateToISO(parsed));
              setTyped('');
            } else {
              setTypedError(true);
            }
          }}
          placeholder={t('tasks.due.placeholder')}
          placeholderTextColor={tokens.muted}
          style={[s.input, typedError ? s.inputError : null]}
          testID="due-date-typed"
        />
      ) : null}
      {typedError ? <Text style={s.error}>{t('tasks.due.invalid')}</Text> : null}
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 6 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  value: { color: tokens.ink, fontSize: 15, flexShrink: 1 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  clear: { color: tokens.ink3, fontWeight: '600', fontSize: 15 },
  dim: { opacity: 0.4 },
  input: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: tokens.surface,
    color: tokens.ink,
  },
  inputError: { borderColor: tokens.bad },
  error: { color: tokens.bad, fontSize: 12 },
});
