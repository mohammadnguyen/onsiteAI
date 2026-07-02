import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import type {
  ExpenseCreateResponse,
  ReviewReasonCode,
} from '../api/hooks/useExpenses';
import { formatMoney } from '../util/format';
import { formatDateAU } from '../util/dates';
import { useScaledStyles } from '../ui/type';

/**
 * Mobile Capture v0: post-submit result card.
 *
 * Stateless presentational component. Mirrors the visual shape of
 * `admin/src/pages/Capture.tsx:ResultView` but in React Native with
 * inline hex colours (Tailwind class equivalents) instead of utility
 * classes.
 *
 * The card has three visual variants driven by `expense.review_status`:
 *  - `reviewed` → green banner, no review-reason chips
 *  - `pending`  → amber banner, chips for each review reason
 *  - anything else (`rejected`, etc.) → amber-pending shape; we do
 *    not silently treat unexpected statuses as success.
 *
 * Reason chip colours mirror `admin/src/pages/Capture.tsx:REASON_COLOR`
 * (Tailwind bg-* / text-* mapped to their hex equivalents).
 */
const REASON_COLORS: Record<ReviewReasonCode, { bg: string; fg: string }> = {
  amount_uncertain: { bg: '#fef3c7', fg: '#92400e' },
  unsupported_currency: { bg: '#ffe4e6', fg: '#9f1239' },
  job_uncertain: { bg: '#e0f2fe', fg: '#075985' },
  supplier_uncertain: { bg: '#ede9fe', fg: '#5b21b6' },
  category_uncertain: { bg: '#ccfbf1', fg: '#115e59' },
  duplicate_suspected: { bg: '#fee2e2', fg: '#991b1b' },
};

// formatMoney moved to mobile/src/util/format.ts and imported above so
// the same helper is used on RecentCapturesList + Expense Detail; the
// shared helper also tightens the non-numeric path to return "—"
// rather than leaking the raw string into the UI.

export function CaptureResultCard({
  result,
  onReset,
  isAdmin,
  jobName,
}: {
  result: ExpenseCreateResponse;
  onReset: () => void;
  // O1-S1: ex-GST / GST breakdown is admin-only. Contributors never
  // render those rows — defence-in-depth on top of the backend
  // server-strip, which already nulls them for non-admin callers.
  isAdmin: boolean;
  // O2-A (feedback #1): the assigned job's display label (zh alias when
  // present, else job name), resolved by the parent. Closes the
  // verification loop — the user sees WHICH job the expense landed on,
  // whether it came from an explicit chip pick or the parser. Job
  // identity is not financial data (contributor-safe).
  jobName?: string;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const expense = result.expense;
  const diagnostics = result.parse;
  const reviewed = expense.review_status === 'reviewed';
  const reasons: ReviewReasonCode[] = diagnostics?.review_reasons ?? [];
  const bannerStyle = reviewed ? s.bannerReviewed : s.bannerPending;
  const bannerText = reviewed
    ? t('capture.result_saved')
    : t('capture.result_pending_review');

  return (
    <View style={s.card} testID="capture-result-card">
      <View style={[s.banner, bannerStyle]}>
        <Text style={[s.bannerText, reviewed ? s.bannerTextReviewed : s.bannerTextPending]}>
          {bannerText}
        </Text>
      </View>

      {reasons.length > 0 ? (
        <View style={s.chipsRow}>
          {reasons.map((code) => {
            const colors = REASON_COLORS[code];
            return (
              <View
                key={code}
                style={[s.chip, { backgroundColor: colors.bg }]}
                testID={`review-reason-${code}`}
              >
                <Text style={[s.chipText, { color: colors.fg }]}>
                  {t(`review_reason.${code}`)}
                </Text>
              </View>
            );
          })}
        </View>
      ) : null}

      <View style={s.fields}>
        {/* #3 saved-vs-typed: amount_inc_gst is the SAVED amount; the
            "You typed" row below echoes the original input so a parser
            misread is visible. Read-only — no re-parse / diff logic. */}
        <FieldRow label={t('expense.amount_inc_gst')} value={formatMoney(expense.amount_inc_gst)} />
        {isAdmin ? (
          <>
            <FieldRow label={t('expense.amount_ex_gst')} value={formatMoney(expense.amount_ex_gst)} />
            <FieldRow label={t('expense.gst')} value={formatMoney(expense.gst_amount)} />
          </>
        ) : null}
        <FieldRow label={t('expense.payment')} value={t(`capture.payment_${expense.payment_method}`, { defaultValue: expense.payment_method })} />
        <FieldRow label={t('expense.date')} value={formatDateAU(expense.expense_date)} />
        {jobName ? (
          <FieldRow label={t('expense.job')} value={jobName} />
        ) : null}
        {expense.raw_input_text ? (
          <FieldRow label={t('capture.you_typed')} value={expense.raw_input_text} />
        ) : null}
        {expense.description ? (
          <FieldRow label={t('expense.description')} value={expense.description} />
        ) : null}
        {expense.duplicate_flag ? (
          <FieldRow
            label={t('expense.duplicate_flag')}
            value={t('common.yes', { defaultValue: 'Yes' })}
          />
        ) : null}
      </View>

      <TouchableOpacity
        onPress={onReset}
        style={s.resetBtn}
        testID="capture-reset"
        accessibilityRole="button"
      >
        {/* Mobile Polish slice: post-save button reads "Capture another"
            (新增 → 继续新增) so the action verb matches the intent of
            starting a fresh entry from the result card. */}
        <Text style={s.resetBtnText}>{t('capture.continue_capture')}</Text>
      </TouchableOpacity>
    </View>
  );
}

function FieldRow({ label, value }: { label: string; value: string | null | undefined }) {
  const s = useScaledStyles(base);
  return (
    <View style={s.fieldRow}>
      <Text style={s.fieldLabel}>{label}</Text>
      <Text style={s.fieldValue} testID={`field-${label}`}>
        {value ?? '—'}
      </Text>
    </View>
  );
}

const base = StyleSheet.create({
  card: {
    backgroundColor: '#ffffff',
    borderRadius: 8,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  banner: {
    borderRadius: 6,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  bannerReviewed: { backgroundColor: '#ecfdf5', borderWidth: 1, borderColor: '#a7f3d0' },
  bannerPending: { backgroundColor: '#fffbeb', borderWidth: 1, borderColor: '#fde68a' },
  bannerText: { fontSize: 16, fontWeight: '600' },
  bannerTextReviewed: { color: '#065f46' },
  bannerTextPending: { color: '#92400e' },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  chipText: { fontSize: 12, fontWeight: '600' },
  fields: {
    borderTopWidth: 1,
    borderTopColor: '#e2e8f0',
    paddingTop: 12,
    gap: 6,
  },
  fieldRow: { flexDirection: 'row', alignItems: 'flex-start' },
  fieldLabel: { flex: 1, color: '#64748b', fontSize: 13 },
  fieldValue: { flex: 2, color: '#0f172a', fontSize: 14 },
  resetBtn: {
    marginTop: 4,
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  resetBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
