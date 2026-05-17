import { useMemo } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  Pressable,
  StyleSheet,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import { useExpense } from '../../src/api/hooks/useExpenses';
import { useJobs } from '../../src/api/hooks/useJobs';
import type {
  ExpenseDetailPublic,
  ReviewReasonCode,
} from '../../src/api/hooks/useExpenses';

/**
 * Mobile Expense Detail (v1) — read-only.
 *
 * Top-level expo-router route at /expenses/[id]. Renders a self-
 * contained header (with a back chevron) instead of relying on a
 * native nav stack, because the project root uses `<Slot />` rather
 * than `<Stack />`. The tab bar slides away while this screen is
 * visible because the route sits outside `(tabs)/` — standard
 * iOS drill-in UX.
 *
 * Hard scope (matches the approved plan):
 *   - read-only fields only
 *   - no edit / delete / resolve / approve / reject / retry-parser
 *   - no receipt / photo upload
 *   - no offline queue
 *
 * ``review_reasons`` semantics (mirrors ADR-equivalent semantics in
 * the backend service): the array reflects the *current*
 * expense_review_queue row's reasons (open / resolved / rejected).
 * Empty when no queue row exists. NOT a historical audit trail —
 * the heading is deliberately "Why this needs review" rather than
 * anything implying a permanent record.
 */

type ReasonColor = { bg: string; fg: string };

const STATUS_COLORS = {
  pending: { bg: '#fef3c7', fg: '#92400e' },
  reviewed: { bg: '#dcfce7', fg: '#15803d' },
  rejected: { bg: '#fee2e2', fg: '#991b1b' },
} as const;

const REASON_COLORS: Record<ReviewReasonCode, ReasonColor> = {
  amount_uncertain: { bg: '#fef3c7', fg: '#92400e' },
  unsupported_currency: { bg: '#ffe4e6', fg: '#9f1239' },
  job_uncertain: { bg: '#e0f2fe', fg: '#075985' },
  supplier_uncertain: { bg: '#ede9fe', fg: '#5b21b6' },
  category_uncertain: { bg: '#ccfbf1', fg: '#115e59' },
  duplicate_suspected: { bg: '#fee2e2', fg: '#991b1b' },
};

function isMissing(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    return status === 404 || status === 403;
  }
  return false;
}

export default function ExpenseDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useTranslation();
  const expense = useExpense(id);
  const jobs = useJobs();

  const jobName = useMemo(() => {
    if (!expense.data) return undefined;
    return jobs.data?.find((j) => j.job_id === expense.data!.job_id)?.job_name;
  }, [expense.data, jobs.data]);

  const onBack = () => {
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/expenses');
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="detail-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('expense.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {expense.isLoading ? (
        <View style={s.state} testID="detail-loading">
          <ActivityIndicator color="#1e293b" />
          <Text style={s.stateText}>{t('common.loading')}</Text>
        </View>
      ) : expense.isError && isMissing(expense.error) ? (
        <View style={s.state} testID="detail-notfound">
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
        <View style={s.state} testID="detail-error">
          <Text style={[s.stateText, s.errorText]}>{t('expense.detail_error')}</Text>
          <Pressable
            onPress={() => void expense.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
            testID="detail-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : expense.data ? (
        <ScrollView contentContainerStyle={s.scroll} testID="detail-content">
          <DetailBody data={expense.data} jobName={jobName} />
        </ScrollView>
      ) : null}
    </SafeAreaView>
  );
}

function DetailBody({
  data,
  jobName,
}: {
  data: ExpenseDetailPublic;
  jobName: string | undefined;
}) {
  const { t } = useTranslation();
  const statusColor = STATUS_COLORS[data.review_status];
  const reasons = data.review_reasons ?? [];
  const showReasons =
    (data.review_status === 'pending' || data.review_status === 'rejected') &&
    reasons.length > 0;

  const paymentLabel =
    data.payment_method === 'cash'
      ? t('capture.payment_cash')
      : data.payment_method === 'transfer'
        ? t('capture.payment_transfer')
        : t('capture.payment_unknown');

  const receiptLabel =
    data.receipt_status === 'expected_later'
      ? t('expense.receipt_expected_later')
      : t('expense.receipt_no_receipt');

  const supplierName = data.supplier?.supplier_name ?? '—';
  const categoryName = data.category?.category_name ?? '—';
  const jobDisplay = jobName ?? data.job_id.slice(0, 8);

  return (
    <>
      <View style={s.hero}>
        <Text style={s.heroAmount} testID="detail-amount">
          {data.amount_inc_gst}
        </Text>
        <View style={[s.pill, { backgroundColor: statusColor.bg }]}>
          <Text style={[s.pillText, { color: statusColor.fg }]}>
            {t(`expense.status_${data.review_status}`)}
          </Text>
        </View>
      </View>

      <View style={s.grid}>
        <Field label={t('expense.amount_ex_gst')} value={data.amount_ex_gst} />
        <Field label={t('expense.gst')} value={data.gst_amount} />
        <Field label={t('expense.date')} value={data.expense_date} />
        <Field label={t('expense.payment')} value={paymentLabel} />
        <Field label={t('expense.supplier')} value={supplierName} />
        <Field label={t('expense.category')} value={categoryName} />
        <Field label={t('expense.job')} value={jobDisplay} />
        <Field label={t('expense.receipt_status')} value={receiptLabel} />
      </View>

      {showReasons && (
        <View style={s.section} testID="detail-reasons">
          <Text style={s.sectionHeading}>{t('expense.review_reasons_heading')}</Text>
          <View style={s.chipsRow}>
            {reasons.map((code) => {
              const color = REASON_COLORS[code];
              return (
                <View
                  key={code}
                  style={[s.chip, { backgroundColor: color.bg }]}
                  testID={`detail-reason-${code}`}
                >
                  <Text style={[s.chipText, { color: color.fg }]}>
                    {t(`review_reason.${code}`)}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      )}

      {(data.duplicate_flag || data.duplicate_of_expense_id) && (
        <View style={[s.section, s.dupBanner]} testID="detail-duplicate">
          {data.duplicate_flag && (
            <Text style={s.dupLine}>{t('capture.recent.duplicate_flag')}</Text>
          )}
          {data.duplicate_of_expense_id && (
            <Text style={s.dupRef}>
              {t('expense.duplicate_of')}: {data.duplicate_of_expense_id.slice(0, 8)}…
            </Text>
          )}
        </View>
      )}

      {data.description ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.description')}</Text>
          <Text style={s.longText}>{data.description}</Text>
        </View>
      ) : null}

      {data.notes ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.notes')}</Text>
          <Text style={s.longText}>{data.notes}</Text>
        </View>
      ) : null}

      {data.raw_input_text ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.raw_input')}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <Text style={s.rawText}>{data.raw_input_text}</Text>
          </ScrollView>
        </View>
      ) : null}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.field}>
      <Text style={s.fieldLabel}>{label}</Text>
      <Text style={s.fieldValue} numberOfLines={2}>
        {value}
      </Text>
    </View>
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
  state: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  scroll: { padding: 16, gap: 20 },
  hero: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  heroAmount: {
    fontSize: 32,
    fontWeight: '700',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  pill: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 14 },
  pillText: { fontSize: 12, fontWeight: '600' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -8 },
  field: { width: '50%', paddingHorizontal: 8, paddingVertical: 8 },
  fieldLabel: {
    fontSize: 11,
    color: '#64748b',
    textTransform: 'uppercase',
    marginBottom: 4,
    fontWeight: '600',
  },
  fieldValue: { fontSize: 15, color: '#0f172a' },
  section: { gap: 8 },
  sectionHeading: {
    fontSize: 13,
    color: '#475569',
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  chipsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  chipText: { fontSize: 12, fontWeight: '600' },
  dupBanner: {
    backgroundColor: '#fef3c7',
    borderColor: '#fde68a',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
  },
  dupLine: { color: '#92400e', fontSize: 14, fontWeight: '600' },
  dupRef: { color: '#92400e', fontSize: 13, marginTop: 4, fontVariant: ['tabular-nums'] },
  longText: { color: '#0f172a', fontSize: 15, lineHeight: 21 },
  rawText: {
    color: '#1e293b',
    fontSize: 12,
    fontFamily: 'Menlo',
    backgroundColor: '#f8fafc',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    borderRadius: 6,
    padding: 8,
  },
});
