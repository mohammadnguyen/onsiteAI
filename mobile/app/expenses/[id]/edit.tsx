import { useEffect, useMemo, useState } from 'react';
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
import { useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useExpense,
  useUpdateExpense,
  type ExpenseUpdateInput,
  type PaymentMethod,
  type ReceiptStatus,
} from '../../../src/api/hooks/useExpenses';
import { useMe } from '../../../src/api/hooks/useAuth';
import { DatePills } from '../../../src/components/DatePills';
import { useOneShotBack } from '../../../src/util/navigation';

/**
 * P4: Mobile Expense Edit screen.
 *
 * Route: ``/expenses/[id]/edit`` (sibling of the detail route at
 * ``/expenses/[id]``). Reachable via the "Edit" button in the
 * detail-screen header.
 *
 * Operator-approved scope (PD-1..PD-8 from the P4 plan packet):
 *   - Editable fields: amount_inc_gst, payment_method, expense_date,
 *     receipt_status, description, notes.
 *   - NOT editable here: supplier, category, job (immutable),
 *     review_status.
 *   - M1: admins editing a non-pending row get an OPTIONAL "reason"
 *     field — audit-only metadata (`ExpenseUpdate.reason`). The
 *     backend never requires it, Save is never blocked on it, and it
 *     is included in the PATCH body only when non-empty. (An earlier
 *     comment claimed the backend 403s reviewed-row edits without a
 *     reason; that check does not exist — corrected in M1.)
 *   - amount_ex_gst / gst_amount: server recomputes from amount_inc_gst.
 *   - PD-7=B — Save is BLOCKED when the DatePills component is in an
 *     invalid state (Other-mode parse error or empty Other input).
 *     This is the explicit anti-silent-drift guarantee: editing is
 *     correction workflow and must NEVER commit a stale "last valid
 *     date" while a wrong-looking string is visible to the user.
 *   - On 200 OK: ``router.back()`` -> detail refreshes via cache
 *     invalidation (PD-5=A).
 *   - On 4xx/5xx: inline error banner, draft preserved (PD-6=A).
 *
 * Conditional-spread body builder: ONLY fields the user actually
 * changed are included in the PATCH body. Unchanged fields are
 * OMITTED entirely (not sent as null) to avoid the Pydantic
 * ``model_fields_set`` trap that would clobber existing values on
 * the backend. ``description`` / ``notes`` cleared by the user ARE
 * sent as null (intentional clear; distinct from "untouched").
 */

type PaymentSel = 'cash' | 'transfer' | 'unknown';

const MAX_AMOUNT = 10_000_000;

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

function parseAmount(s: string): number | null {
  if (s.trim().length === 0) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  if (n <= 0 || n > MAX_AMOUNT) return null;
  return n;
}

function isMissing(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    return status === 404 || status === 403;
  }
  return false;
}

export default function ExpenseEditScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const expense = useExpense(id);
  const update = useUpdateExpense(id ?? '');
  const me = useMe();

  // Form state. Seeded from expense.data once after first load.
  const [amountText, setAmountText] = useState<string>('');
  const [expenseDate, setExpenseDate] = useState<string>('');
  const [paymentSel, setPaymentSel] = useState<PaymentSel>('unknown');
  const [receiptLater, setReceiptLater] = useState<boolean>(false);
  const [description, setDescription] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  // M1: optional audit reason for admin edits of non-pending rows.
  // Deliberately NOT part of the diff builder — typing a reason alone
  // never dirties the form, and an empty reason never blocks Save
  // (the backend treats ExpenseUpdate.reason as OPTIONAL audit-only
  // metadata; permissions stay server-side).
  const [reason, setReason] = useState<string>('');

  // Validity signals from sub-components / our own parsers.
  const [dateValid, setDateValid] = useState<boolean>(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [initialized, setInitialized] = useState<boolean>(false);

  // Seed once on first successful load. Avoids stomping user edits if
  // the query refetches in the background (e.g. after window focus).
  useEffect(() => {
    if (!expense.data || initialized) return;
    const e = expense.data;
    setAmountText(Number(e.amount_inc_gst).toFixed(2));
    setExpenseDate(e.expense_date);
    setPaymentSel((e.payment_method ?? 'unknown') as PaymentSel);
    setReceiptLater(e.receipt_status === 'expected_later');
    setDescription(e.description ?? '');
    setNotes(e.notes ?? '');
    setInitialized(true);
  }, [expense.data, initialized]);

  const amountNum = useMemo(() => parseAmount(amountText), [amountText]);
  const amountValid = amountNum !== null;

  // Conditional-spread body diff. Returns null when no field has been
  // touched, which (alongside the validity gates) keeps Save disabled
  // on a pristine form and prevents wasted PATCH round-trips.
  const diff = useMemo<ExpenseUpdateInput | null>(() => {
    if (!expense.data || !initialized || amountNum === null) return null;
    const e = expense.data;
    const out: ExpenseUpdateInput = {};
    if (Number(e.amount_inc_gst) !== amountNum) {
      out.amount_inc_gst = amountNum;
    }
    if (expenseDate !== e.expense_date) {
      out.expense_date = expenseDate;
    }
    if ((paymentSel as PaymentMethod) !== e.payment_method) {
      out.payment_method = paymentSel as PaymentMethod;
    }
    const desiredReceipt: ReceiptStatus = receiptLater
      ? 'expected_later'
      : 'no_receipt';
    if (desiredReceipt !== e.receipt_status) {
      out.receipt_status = desiredReceipt;
    }
    const desiredDesc: string | null =
      description.trim().length === 0 ? null : description;
    if (desiredDesc !== (e.description ?? null)) {
      out.description = desiredDesc;
    }
    const desiredNotes: string | null =
      notes.trim().length === 0 ? null : notes;
    if (desiredNotes !== (e.notes ?? null)) {
      out.notes = desiredNotes;
    }
    return Object.keys(out).length === 0 ? null : out;
  }, [
    expense.data,
    initialized,
    amountNum,
    expenseDate,
    paymentSel,
    receiptLater,
    description,
    notes,
  ]);

  // M1: show the optional reason field only to admins editing a row
  // that has already left `pending`. Role data comes from the cached
  // /auth/me query and drives VISIBILITY ONLY — the backend service
  // remains the authority on what an edit may do.
  const showReasonField =
    me.data?.role === 'admin' &&
    !!expense.data &&
    expense.data.review_status !== 'pending';

  // One-shot back (util/navigation): also covers onSave's post-PATCH
  // back racing a manual chevron tap. Deep-link / cold-launch fallback:
  // there's no detail-back history to consume, so we route to the
  // expenses tab rather than to /expenses/{id} (the latter would also
  // be valid but requires the typed-routes manifest cast pattern used
  // elsewhere — same UX, no cast).
  const onBack = useOneShotBack('/(tabs)/expenses');

  const onSave = async () => {
    if (update.isPending) return;
    if (!amountValid || !dateValid || diff === null) return;
    setFormError(null);
    Keyboard.dismiss();
    try {
      // M1: attach the optional audit reason only when the field is
      // visible AND non-empty after trimming. `reason` is NOT in the
      // diff builder, so it can never dirty a pristine form or block
      // Save on its own. Cap at the backend's max_length=500.
      const trimmedReason = reason.trim();
      const body: ExpenseUpdateInput =
        showReasonField && trimmedReason.length > 0
          ? { ...diff, reason: trimmedReason.slice(0, 500) }
          : diff;
      await update.mutateAsync(body);
      onBack();
    } catch (err) {
      setFormError(extractErrorMessage(err, t('edit.error_network')));
    }
  };

  // Save gate (PD-7=B is the key clause):
  //   - mutation in flight
  //   - amount field doesn't parse to a valid Decimal (> 0, <= 10M)
  //   - DatePills reports invalid state -> Save BLOCKED
  //   - form is pristine (diff null) -> nothing to send
  const saveDisabled =
    update.isPending || !amountValid || !dateValid || diff === null;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="edit-back"
          accessibilityRole="button"
          accessibilityLabel={t('edit.cancel')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('edit.cancel')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('edit.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {expense.isLoading ? (
        <View style={s.state} testID="edit-loading">
          <ActivityIndicator color="#1e293b" />
          <Text style={s.stateText}>{t('common.loading')}</Text>
        </View>
      ) : expense.isError && isMissing(expense.error) ? (
        <View style={s.state} testID="edit-notfound">
          <Text style={s.stateText}>{t('expense.not_found')}</Text>
          <Pressable
            onPress={onBack}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
          >
            <Text style={s.linkBtnText}>{t('expense.back')}</Text>
          </Pressable>
        </View>
      ) : expense.isError ? (
        <View style={s.state} testID="edit-error">
          <Text style={[s.stateText, s.errorText]}>
            {t('expense.detail_error')}
          </Text>
          <Pressable
            onPress={() => void expense.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
            testID="edit-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : expense.data ? (
        <KeyboardAvoidingView
          style={s.flex}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <ScrollView
            contentContainerStyle={s.scroll}
            keyboardShouldPersistTaps="handled"
            testID="edit-form"
          >
            <Text style={s.label}>{t('expense.amount_inc_gst')}</Text>
            <TextInput
              value={amountText}
              onChangeText={setAmountText}
              keyboardType="decimal-pad"
              placeholder="0.00"
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              style={[
                s.input,
                !amountValid && amountText.length > 0 ? s.inputError : null,
              ]}
              testID="edit-amount"
              accessibilityLabel={t('expense.amount_inc_gst')}
            />
            {!amountValid && amountText.length > 0 ? (
              <Text style={s.fieldError} testID="edit-amount-error">
                {t('edit.amount_invalid')}
              </Text>
            ) : null}

            <DatePills
              value={expenseDate}
              onChange={setExpenseDate}
              onValidityChange={setDateValid}
              disabled={update.isPending}
            />

            <View style={s.paymentRow}>
              <Text style={s.paymentLabel}>{t('capture.payment_label')}</Text>
              <PaymentOption
                label={t('capture.payment_cash')}
                active={paymentSel === 'cash'}
                disabled={update.isPending}
                onPress={() => setPaymentSel('cash')}
                testID="edit-payment-cash"
              />
              <PaymentOption
                label={t('capture.payment_transfer')}
                active={paymentSel === 'transfer'}
                disabled={update.isPending}
                onPress={() => setPaymentSel('transfer')}
                testID="edit-payment-transfer"
              />
              <PaymentOption
                label={t('capture.payment_unknown')}
                active={paymentSel === 'unknown'}
                disabled={update.isPending}
                onPress={() => setPaymentSel('unknown')}
                testID="edit-payment-unknown"
              />
            </View>

            <TouchableOpacity
              style={s.checkboxRow}
              onPress={() =>
                !update.isPending && setReceiptLater((v) => !v)
              }
              disabled={update.isPending}
              testID="edit-receipt-later"
              accessibilityRole="checkbox"
              accessibilityState={{ checked: receiptLater }}
            >
              <View
                style={[s.checkbox, receiptLater && s.checkboxChecked]}
              >
                {receiptLater ? (
                  <Text style={s.checkmark}>{'✓'}</Text>
                ) : null}
              </View>
              <Text style={s.checkboxLabel}>{t('capture.receipt_later')}</Text>
            </TouchableOpacity>

            <Text style={s.label}>{t('expense.description')}</Text>
            <TextInput
              value={description}
              onChangeText={setDescription}
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              multiline
              style={[s.input, s.multiline]}
              testID="edit-description"
              accessibilityLabel={t('expense.description')}
            />

            <Text style={s.label}>{t('expense.notes')}</Text>
            <TextInput
              value={notes}
              onChangeText={setNotes}
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              multiline
              style={[s.input, s.multiline]}
              testID="edit-notes"
              accessibilityLabel={t('expense.notes')}
            />

            {showReasonField ? (
              <>
                <Text style={s.label}>{t('expense.edit_reason_label')}</Text>
                <TextInput
                  value={reason}
                  onChangeText={setReason}
                  placeholderTextColor="#94a3b8"
                  editable={!update.isPending}
                  maxLength={500}
                  style={s.input}
                  testID="edit-reason"
                  accessibilityLabel={t('expense.edit_reason_label')}
                />
                <Text style={s.reasonHelp}>
                  {t('expense.edit_reason_help')}
                </Text>
              </>
            ) : null}

            {formError ? (
              <View style={s.errorBanner} testID="edit-error-banner">
                <Text style={s.errorBannerText}>{formError}</Text>
              </View>
            ) : null}

            <TouchableOpacity
              onPress={onSave}
              disabled={saveDisabled}
              style={[s.saveBtn, saveDisabled && s.saveBtnDisabled]}
              testID="edit-save"
              accessibilityRole="button"
              accessibilityState={{ disabled: saveDisabled }}
            >
              {update.isPending ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={s.saveBtnText}>{t('edit.save')}</Text>
              )}
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      ) : null}
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
      style={[
        s.paymentOption,
        active && s.paymentOptionActive,
        disabled && s.paymentOptionDisabled,
      ]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text
        style={[s.paymentOptionText, active && s.paymentOptionTextActive]}
      >
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
  state: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 12,
  },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
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
  inputError: { borderColor: '#dc2626' },
  multiline: { minHeight: 80, textAlignVertical: 'top' },
  fieldError: { color: '#b91c1c', fontSize: 13 },
  reasonHelp: { color: '#64748b', fontSize: 12 },
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
  errorBannerText: { color: '#991b1b', fontSize: 14 },
  saveBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
    marginTop: 8,
  },
  saveBtnDisabled: { opacity: 0.4 },
  saveBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
