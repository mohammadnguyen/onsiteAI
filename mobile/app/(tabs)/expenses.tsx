import { useRef, useState } from 'react';
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
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import {
  useCreateExpense,
  useMyRecentExpenses,
  type ExpenseCreateInput,
  type ExpenseCreateResponse,
  type PaymentMethod,
  type ReceiptStatus,
} from '../../src/api/hooks/useExpenses';
import { CaptureResultCard } from '../../src/components/CaptureResultCard';
import { RecentCapturesList } from '../../src/components/RecentCapturesList';

/**
 * Mobile Capture v0: natural-language expense capture screen.
 *
 * State machine is driven by `useMutation` flags + a single `result`
 * value. No reducer.
 *
 * Mirrors the shape of `admin/src/pages/Capture.tsx` (also v0-scoped to
 * raw_input_text + payment + receipt-later) but in React Native. The
 * conditional-spread body builder is the documented workaround for
 * the Pydantic `model_fields_set` 422 trap — sending explicit `null`
 * for unset optional fields marks them as "caller-set" and overrides
 * the parser's value, causing spurious "Amount is required" 422s.
 */

type PaymentSel = 'auto' | 'cash' | 'transfer';

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d: { msg?: string; loc?: (string | number)[] }) => {
          const loc = Array.isArray(d.loc) ? d.loc.join('.') : '';
          return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '');
        })
        .join('; ');
    }
    if (error.message) return error.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export default function ExpensesScreen() {
  const { t } = useTranslation();
  const createExpense = useCreateExpense();
  // Mobile Capture v1 Sub-batch A: "My Captures" list query lives on
  // the parent screen so the same RefreshControl can drive pull-to-
  // refresh from anywhere in the scrollable area (form region or
  // list region). `useCreateExpense` already invalidates the
  // ['expenses'] root, so a successful capture auto-refetches this
  // query without extra wiring.
  const recentExpenses = useMyRecentExpenses(20);
  const textareaRef = useRef<TextInput>(null);

  const [rawInputText, setRawInputText] = useState('');
  const [paymentSel, setPaymentSel] = useState<PaymentSel>('auto');
  const [receiptLater, setReceiptLater] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<ExpenseCreateResponse | null>(null);

  // RefreshControl reads `isRefetching` (not `isFetching`) so the
  // spinner only shows on manual pull-to-refresh, not on initial
  // load — initial load is covered by the list's own loading state.
  const refreshControl = (
    <RefreshControl
      refreshing={recentExpenses.isRefetching}
      onRefresh={() => void recentExpenses.refetch()}
      tintColor="#1e293b"
    />
  );

  const onSubmit = async () => {
    if (createExpense.isPending) return;
    const trimmed = rawInputText.trim();
    if (trimmed.length === 0) return;
    setFormError(null);
    Keyboard.dismiss();

    // Conditional-spread body builder: only fields the user actually
    // set are included. Sending explicit `null` would mark them as
    // caller-set in the backend's `model_fields_set` and cause a 422.
    // See `admin/src/pages/Capture.tsx:98-106` for the canonical
    // version.
    type CaptureBody = Omit<ExpenseCreateInput, 'payment_method'> & {
      payment_method?: PaymentMethod;
    };
    const body: CaptureBody = {
      raw_input_text: trimmed,
      expense_type: 'supplier_expense',
      receipt_status: (receiptLater ? 'expected_later' : 'no_receipt') as ReceiptStatus,
    };
    if (paymentSel !== 'auto') body.payment_method = paymentSel as PaymentMethod;

    try {
      const resp = await createExpense.mutateAsync(body as ExpenseCreateInput);
      setResult(resp);
    } catch (err) {
      setFormError(extractErrorMessage(err, t('capture.error_network')));
    }
  };

  const onReset = () => {
    setRawInputText('');
    setPaymentSel('auto');
    setReceiptLater(false);
    setFormError(null);
    setResult(null);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  if (result) {
    return (
      <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          <Text style={s.title}>{t('capture.title')}</Text>
          <CaptureResultCard result={result} onReset={onReset} />
          <RecentCapturesList query={recentExpenses} />
        </ScrollView>
      </SafeAreaView>
    );
  }

  const submitDisabled = createExpense.isPending || rawInputText.trim().length === 0;

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <KeyboardAvoidingView
        style={s.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          <Text style={s.title}>{t('capture.title')}</Text>

          <TextInput
            ref={textareaRef}
            value={rawInputText}
            onChangeText={setRawInputText}
            placeholder={t('capture.textarea_placeholder')}
            placeholderTextColor="#94a3b8"
            multiline
            autoFocus
            editable={!createExpense.isPending}
            style={s.textarea}
            testID="capture-textarea"
            accessibilityLabel={t('capture.title')}
          />

          <View style={s.paymentRow}>
            <Text style={s.paymentLabel}>{t('capture.payment_label')}</Text>
            <PaymentOption
              label={t('capture.payment_auto')}
              active={paymentSel === 'auto'}
              disabled={createExpense.isPending}
              onPress={() => setPaymentSel('auto')}
              testID="payment-auto"
            />
            <PaymentOption
              label={t('capture.payment_cash')}
              active={paymentSel === 'cash'}
              disabled={createExpense.isPending}
              onPress={() => setPaymentSel('cash')}
              testID="payment-cash"
            />
            <PaymentOption
              label={t('capture.payment_transfer')}
              active={paymentSel === 'transfer'}
              disabled={createExpense.isPending}
              onPress={() => setPaymentSel('transfer')}
              testID="payment-transfer"
            />
          </View>

          <TouchableOpacity
            style={s.checkboxRow}
            onPress={() => !createExpense.isPending && setReceiptLater((v) => !v)}
            disabled={createExpense.isPending}
            testID="receipt-later"
            accessibilityRole="checkbox"
            accessibilityState={{ checked: receiptLater }}
          >
            <View style={[s.checkbox, receiptLater && s.checkboxChecked]}>
              {receiptLater ? <Text style={s.checkmark}>{'✓'}</Text> : null}
            </View>
            <Text style={s.checkboxLabel}>{t('capture.receipt_later')}</Text>
          </TouchableOpacity>

          {formError ? (
            <View style={s.errorBanner} testID="capture-error">
              <Text style={s.errorText}>{formError}</Text>
            </View>
          ) : null}

          <TouchableOpacity
            onPress={onSubmit}
            disabled={submitDisabled}
            style={[s.submitBtn, submitDisabled && s.submitBtnDisabled]}
            testID="capture-submit"
            accessibilityRole="button"
          >
            {createExpense.isPending ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.submitBtnText}>{t('capture.submit')}</Text>
            )}
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function PaymentOption({
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
      style={[s.paymentOption, active && s.paymentOptionActive, disabled && s.paymentOptionDisabled]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text style={[s.paymentOptionText, active && s.paymentOptionTextActive]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  flex: { flex: 1 },
  scroll: { padding: 16, gap: 14 },
  title: {
    fontSize: 24,
    fontWeight: '600',
    color: '#0f172a',
    marginBottom: 4,
  },
  textarea: {
    minHeight: 120,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    color: '#0f172a',
    backgroundColor: '#ffffff',
    textAlignVertical: 'top',
  },
  paymentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  paymentLabel: { color: '#475569', fontSize: 14, marginRight: 4 },
  paymentOption: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  paymentOptionActive: {
    backgroundColor: '#1e293b',
    borderColor: '#1e293b',
  },
  paymentOptionDisabled: { opacity: 0.5 },
  paymentOptionText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  paymentOptionTextActive: { color: '#ffffff' },
  checkboxRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 4,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderWidth: 1.5,
    borderColor: '#94a3b8',
    borderRadius: 4,
    marginRight: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxChecked: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  checkmark: { color: '#ffffff', fontSize: 14, fontWeight: '700' },
  checkboxLabel: { color: '#0f172a', fontSize: 14 },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 12,
  },
  errorText: { color: '#991b1b', fontSize: 14 },
  submitBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
  },
  submitBtnDisabled: { opacity: 0.4 },
  submitBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
