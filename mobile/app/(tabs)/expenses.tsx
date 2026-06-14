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
import {
  useCreateExpense,
  useMyRecentExpenses,
  type ExpenseCreateInput,
  type ExpenseCreateResponse,
  type PaymentMethod,
  type ReceiptStatus,
} from '../../src/api/hooks/useExpenses';
import { resolveApiErrorMessage } from '../../src/api/errors';
import { useMe } from '../../src/api/hooks/useAuth';
import { CaptureResultCard } from '../../src/components/CaptureResultCard';
import { RecentCapturesList } from '../../src/components/RecentCapturesList';
import { RecentFailuresList } from '../../src/components/RecentFailuresList';
import { DatePills } from '../../src/components/DatePills';
import { useFailuresStore } from '../../src/store/failures';
import { todayISO } from '../../src/util/dates';

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

/**
 * Aggregated result of a multi-item capture submission.
 *
 * Path A (mobile-only) approach: mobile splits raw_input_text on
 * newlines, treats the first line as a shared preamble when it has
 * no `$` (e.g. just a job ref like `003`), prepends it to each item
 * line, and fires N POST /expenses calls in parallel. Each item's
 * settled state (saved or error) is captured here so the result card
 * can render per-row status. Backend untouched — every item goes
 * through the existing single-expense pipeline.
 */
type MultiCaptureItem = {
  text: string;
  success: boolean;
  expense?: ExpenseCreateResponse['expense'];
  reviewPending?: boolean;
  error?: string;
};

type MultiCaptureResult = {
  items: MultiCaptureItem[];
  preamble: string | null;
};

export default function ExpensesScreen() {
  const { t } = useTranslation();
  const createExpense = useCreateExpense();
  // Mobile Capture v1 Sub-batch A: "My Captures" list query lives on
  // the parent screen so the same RefreshControl can drive pull-to-
  // refresh from anywhere in the scrollable area (form region or
  // list region). `useCreateExpense` already invalidates the
  // ['expenses'] root, so a successful capture auto-refetches this
  // query without extra wiring.
  //
  // Limit 5 (was 20): operator dogfood signal — on the Capture screen,
  // "My Captures" functions as a quick-access shortcut to recently
  // captured items for correction, not a comprehensive list. 5 is
  // enough for "what did I just enter". Per-job expense list in the
  // Job detail modal stays at 20 (different context: comprehensive
  // per-job view).
  const recentExpenses = useMyRecentExpenses(5);
  // M3: admin-only triage entry. /auth/me drives VISIBILITY ONLY —
  // the review-queue backend routes stay authoritative (403 for
  // contributors). Hidden while the role is loading (fails closed).
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  // M0: persisted failed-capture store — failures recorded here stay
  // visible after form reset and app restart (see src/store/failures).
  const recordFailure = useFailuresStore((st) => st.recordFailure);
  const textareaRef = useRef<TextInput>(null);

  const [rawInputText, setRawInputText] = useState('');
  const [paymentSel, setPaymentSel] = useState<PaymentSel>('auto');
  const [receiptLater, setReceiptLater] = useState(false);
  // P3: expense_date is always set client-side (defaults to today's
  // local ISO) and always sent in the body, so the backend never has
  // to fall back to its own date.today() default for mobile captures.
  // DatePills enforces that this only holds a valid ISO YYYY-MM-DD.
  const [expenseDate, setExpenseDate] = useState<string>(() => todayISO());
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<ExpenseCreateResponse | null>(null);
  // Multi-item capture (Path A — mobile-only, N parallel API calls).
  // When the user types multi-line input, mobile splits on newlines,
  // treats the first line as a shared preamble if it has no $, then
  // POSTs one expense per item line in parallel. Aggregated result
  // replaces the single-item result card. Backend untouched.
  const [multiResult, setMultiResult] = useState<MultiCaptureResult | null>(
    null,
  );
  const [multiPending, setMultiPending] = useState(false);

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

  // Conditional-spread body builder: only fields the user actually
  // set are included. Sending explicit `null` would mark them as
  // caller-set in the backend's `model_fields_set` and cause a 422.
  // See `admin/src/pages/Capture.tsx:98-106` for the canonical
  // version. Shared by single + multi paths.
  type CaptureBody = Omit<ExpenseCreateInput, 'payment_method'> & {
    payment_method?: PaymentMethod;
  };
  const buildBody = (rawText: string): CaptureBody => {
    const body: CaptureBody = {
      raw_input_text: rawText,
      expense_type: 'supplier_expense',
      receipt_status: (receiptLater
        ? 'expected_later'
        : 'no_receipt') as ReceiptStatus,
      expense_date: expenseDate,
    };
    if (paymentSel !== 'auto') body.payment_method = paymentSel as PaymentMethod;
    return body;
  };

  const onSubmit = async () => {
    if (createExpense.isPending || multiPending) return;
    const trimmed = rawInputText.trim();
    if (trimmed.length === 0) return;
    setFormError(null);
    Keyboard.dismiss();

    // Multi-item detection: split on newlines, filter empty lines.
    const lines = trimmed
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    if (lines.length <= 1) {
      // Single-item path — unchanged behaviour.
      const body = buildBody(trimmed);
      try {
        const resp = await createExpense.mutateAsync(body as ExpenseCreateInput);
        setResult(resp);
      } catch (err) {
        const msg = resolveApiErrorMessage(err, t, t('capture.error_network'));
        setFormError(msg);
        // M0: persist the failed capture (typed text + error message)
        // so it survives reset/restart and can be refilled for retry.
        recordFailure({ inputText: trimmed, errorMessage: msg, context: 'single' });
      }
      return;
    }

    // Multi-item path. First-line preamble detection: a line is a
    // preamble if it has no `$` (operator's pattern is a bare job
    // ref like `003` on line 1). The preamble is prepended to each
    // subsequent item line so the backend parser still receives a
    // complete single-item string with the job context attached.
    // If the first line itself contains `$`, every line is treated
    // as an independent complete item.
    const firstLine = lines[0];
    const hasPreamble = !firstLine.includes('$');
    const itemLines = hasPreamble ? lines.slice(1) : lines;
    const preamble = hasPreamble ? firstLine : null;

    if (itemLines.length === 0) {
      // Preamble-only input — nothing to submit.
      setFormError(t('capture.multi_no_items'));
      return;
    }

    const itemTexts = itemLines.map((line) =>
      preamble ? `${preamble} ${line}` : line,
    );

    setMultiPending(true);
    try {
      // Parallel POSTs. Promise.allSettled-equivalent via per-item
      // try/catch so one failure doesn't drop the entire batch.
      const results = await Promise.all(
        itemTexts.map(async (text): Promise<MultiCaptureItem> => {
          try {
            const resp = await createExpense.mutateAsync(
              buildBody(text) as ExpenseCreateInput,
            );
            return {
              text,
              success: true,
              expense: resp.expense,
              reviewPending: resp.expense.review_status === 'pending',
            };
          } catch (err) {
            const msg = resolveApiErrorMessage(err, t, t('capture.error_network'));
            // M0: persist each failed item for visibility/retry after
            // the result card is dismissed or the app restarts.
            recordFailure({ inputText: text, errorMessage: msg, context: 'multi' });
            return {
              text,
              success: false,
              error: msg,
            };
          }
        }),
      );
      setMultiResult({ items: results, preamble });
    } finally {
      setMultiPending(false);
    }
  };

  const onReset = () => {
    setRawInputText('');
    setPaymentSel('auto');
    setReceiptLater(false);
    // P3: reset the date back to today on a fresh capture — anchoring
    // the form on "now" matches the iOS-first on-site flow.
    setExpenseDate(todayISO());
    setFormError(null);
    setResult(null);
    setMultiResult(null);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  // M0: put a failed capture's original text back into the form for a
  // retry. Clears any stale error banner; deliberately keeps the
  // payment/date/receipt selections as the user last set them.
  const onRefillFailure = (text: string) => {
    setRawInputText(text);
    setFormError(null);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  if (multiResult) {
    return (
      <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          <Text style={s.title}>{t('capture.title')}</Text>
          <MultiCaptureResultCard
            result={multiResult}
            onReset={onReset}
          />
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

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
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

  // Unified in-flight flag: blocks form interaction during BOTH the
  // single-item mutation (createExpense.isPending) and the multi-item
  // parallel batch (multiPending). Used by the submit button + every
  // form input below.
  const inFlight = createExpense.isPending || multiPending;
  const submitDisabled = inFlight || rawInputText.trim().length === 0;

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
            editable={!inFlight}
            style={s.textarea}
            testID="capture-textarea"
            accessibilityLabel={t('capture.title')}
          />

          <DatePills
            value={expenseDate}
            onChange={setExpenseDate}
            disabled={inFlight}
          />

          <View style={s.paymentRow}>
            <Text style={s.paymentLabel}>{t('capture.payment_label')}</Text>
            <PaymentOption
              label={t('capture.payment_auto')}
              active={paymentSel === 'auto'}
              disabled={inFlight}
              onPress={() => setPaymentSel('auto')}
              testID="payment-auto"
            />
            <PaymentOption
              label={t('capture.payment_cash')}
              active={paymentSel === 'cash'}
              disabled={inFlight}
              onPress={() => setPaymentSel('cash')}
              testID="payment-cash"
            />
            <PaymentOption
              label={t('capture.payment_transfer')}
              active={paymentSel === 'transfer'}
              disabled={inFlight}
              onPress={() => setPaymentSel('transfer')}
              testID="payment-transfer"
            />
          </View>

          <TouchableOpacity
            style={s.checkboxRow}
            onPress={() => !inFlight && setReceiptLater((v) => !v)}
            disabled={inFlight}
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
            {inFlight ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.submitBtnText}>{t('capture.submit')}</Text>
            )}
          </TouchableOpacity>

          {/* M0: persisted failed captures (if any) — tap a row to put
              the text back into the form and retry it. Renders nothing
              when there are no stored failures. */}
          <RecentFailuresList onRefill={onRefillFailure} />

          {/* Correction-loop fix: render My Captures below the form even
              in the pre-submit (empty form) state. Previously this list
              only appeared after a successful capture, which meant the
              user had no way to navigate to a past expense detail
              without first capturing a new one. With the list always
              visible, the path "I want to fix something I captured
              earlier" -> scroll -> tap row -> detail -> Edit expense
              is one tap from the default state of the Capture tab. */}
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
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

/**
 * Result card for a multi-item capture submission.
 *
 * Renders aggregated counts (saved / total / total $) + a per-row
 * list with success / review-pending / failure status. Each row
 * shows the literal input text the parser received (preamble +
 * item line concatenation) so the user can spot what went wrong on
 * a failed item without having to remember what they typed.
 *
 * Reset returns the user to the empty capture form.
 */
function MultiCaptureResultCard({
  result,
  onReset,
}: {
  result: MultiCaptureResult;
  onReset: () => void;
}) {
  const { t } = useTranslation();
  const total = result.items.length;
  const saved = result.items.filter((i) => i.success).length;
  const totalSpend = result.items.reduce((acc, i) => {
    if (i.success && i.expense) return acc + Number(i.expense.amount_inc_gst);
    return acc;
  }, 0);
  const anyFailed = saved < total;
  return (
    <View style={s.multiCard} testID="multi-capture-result-card">
      <View
        style={[
          s.multiBanner,
          anyFailed ? s.multiBannerMixed : s.multiBannerOk,
        ]}
      >
        <Text
          style={[
            s.multiBannerText,
            anyFailed ? s.multiBannerTextMixed : s.multiBannerTextOk,
          ]}
        >
          {t('capture.multi_result_summary', { saved, total })}
        </Text>
        <Text style={s.multiBannerSubtle}>
          {t('capture.multi_result_total', {
            amount: `$${totalSpend.toFixed(2)}`,
          })}
        </Text>
      </View>
      {result.preamble ? (
        <Text style={s.multiPreamble}>
          {t('capture.multi_preamble_label', { preamble: result.preamble })}
        </Text>
      ) : null}
      <View style={s.multiItems}>
        {result.items.map((item, idx) => (
          <View
            key={idx}
            style={s.multiItemRow}
            testID={`multi-item-${idx}`}
          >
            <Text
              style={[
                s.multiItemMark,
                item.success ? s.multiItemMarkOk : s.multiItemMarkFail,
              ]}
            >
              {item.success ? '✓' : '✗'}
            </Text>
            <View style={s.multiItemBody}>
              <Text style={s.multiItemText} numberOfLines={2}>
                {item.text}
              </Text>
              {item.success && item.expense ? (
                <Text style={s.multiItemMeta}>
                  ${Number(item.expense.amount_inc_gst).toFixed(2)}
                  {item.reviewPending
                    ? ` · ${t('capture.result_pending_review')}`
                    : ''}
                </Text>
              ) : null}
              {!item.success && item.error ? (
                <Text style={s.multiItemError}>{item.error}</Text>
              ) : null}
            </View>
          </View>
        ))}
      </View>
      <TouchableOpacity
        onPress={onReset}
        style={s.resetBtn}
        testID="multi-capture-reset"
        accessibilityRole="button"
      >
        <Text style={s.resetBtnText}>{t('capture.continue_capture')}</Text>
      </TouchableOpacity>
    </View>
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
  // Multi-item capture result card
  multiCard: {
    backgroundColor: '#ffffff',
    borderRadius: 8,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  multiBanner: {
    borderRadius: 6,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderWidth: 1,
  },
  multiBannerOk: {
    backgroundColor: '#ecfdf5',
    borderColor: '#a7f3d0',
  },
  multiBannerMixed: {
    backgroundColor: '#fffbeb',
    borderColor: '#fde68a',
  },
  multiBannerText: { fontSize: 16, fontWeight: '600' },
  multiBannerTextOk: { color: '#065f46' },
  multiBannerTextMixed: { color: '#92400e' },
  multiBannerSubtle: { color: '#475569', fontSize: 13, marginTop: 4 },
  multiPreamble: { color: '#475569', fontSize: 13 },
  multiItems: {
    borderTopWidth: 1,
    borderTopColor: '#e2e8f0',
    paddingTop: 8,
    gap: 10,
  },
  multiItemRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  multiItemMark: { fontSize: 16, fontWeight: '700', width: 16 },
  multiItemMarkOk: { color: '#15803d' },
  multiItemMarkFail: { color: '#b91c1c' },
  multiItemBody: { flex: 1 },
  multiItemText: { color: '#0f172a', fontSize: 14 },
  multiItemMeta: {
    color: '#64748b',
    fontSize: 13,
    marginTop: 2,
    fontVariant: ['tabular-nums'],
  },
  multiItemError: { color: '#b91c1c', fontSize: 13, marginTop: 2 },
  resetBtn: {
    marginTop: 4,
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  resetBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
