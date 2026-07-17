import { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  Pressable,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Link, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useOpenReviewQueue,
  usePendingExpenseSummaries,
  type ReviewQueueItem,
} from '../src/api/hooks/useReviewQueue';
import { useJobs } from '../src/api/hooks/useJobs';
import type { ExpensePublic } from '../src/api/hooks/useExpenses';
import { formatMoney } from '../src/util/format';
import { formatDateAU } from '../src/util/dates';
import { useOneShotBack } from '../src/util/navigation';
import { tokens } from '../src/ui/tokens';

/**
 * M3: mobile review queue / pending triage (admin-only).
 *
 * Route: ``/review-queue``. Entered via the admin-only "Pending
 * review" link under the Last-5 list on the Expenses tab.
 *
 * Option 1 client-side join (operator decision): queue rows from
 * GET /review-queue?status=open (ordering + review reasons) are
 * joined by expense_id against GET /expenses?status=pending
 * (summaries). Rows whose summary is missing (join miss — only
 * possible past the 500-row page cap) render DEGRADED: reason chips
 * + waiting-since + a "details unavailable" line, still tappable.
 *
 * Each row links to the existing expense detail screen, where the
 * M1 Approve / Reject / Edit flows already live — this screen adds
 * NO new mutation paths. After any action, the ['review-queue'] and
 * ['expenses'] cache roots are invalidated by the existing hooks, so
 * returning to this screen shows fresh data.
 *
 * Non-admins never see the entry link; if one lands here anyway the
 * backend 403s the queue fetch and the screen shows the existing
 * "admins only" message — backend remains authoritative.
 */

export default function ReviewQueueScreen() {
  const { t } = useTranslation();

  const queue = useOpenReviewQueue();
  const summaries = usePendingExpenseSummaries();
  const jobs = useJobs();
  const [userRefreshing, setUserRefreshing] = useState(false);

  const jobMap = useMemo(() => {
    const m = new Map<string, string>();
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name));
    return m;
  }, [jobs.data]);

  const expenseMap = useMemo(() => {
    const m = new Map<string, ExpensePublic>();
    summaries.data?.items.forEach((e) => m.set(e.expense_id, e));
    return m;
  }, [summaries.data]);

  const onBack = useOneShotBack('/(tabs)/home' as unknown as Href);

  const onRefresh = () => {
    setUserRefreshing(true);
    void Promise.allSettled([queue.refetch(), summaries.refetch()]).finally(
      () => setUserRefreshing(false),
    );
  };

  const isForbidden =
    queue.isError &&
    axios.isAxiosError(queue.error) &&
    queue.error.response?.status === 403;

  // Wait for BOTH initial loads so enriched rows don't pop in after
  // a degraded first paint. A summaries FAILURE is non-fatal: rows
  // render degraded (the queue itself is the source of truth here).
  const loading = queue.isLoading || summaries.isLoading;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="review-queue-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('review_queue.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      <FlatList
        data={loading ? [] : (queue.data ?? [])}
        keyExtractor={(item) => item.review_id}
        renderItem={({ item }) => (
          <TriageRow
            item={item}
            expense={expenseMap.get(item.expense_id)}
            jobMap={jobMap}
          />
        )}
        style={s.list}
        contentContainerStyle={
          loading || (queue.data ?? []).length === 0
            ? s.listEmptyContainer
            : s.listContainer
        }
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor="#1e293b"
          />
        }
        testID="review-queue-list"
        ListEmptyComponent={
          loading ? (
            <View style={s.state} testID="review-queue-loading">
              <ActivityIndicator color="#1e293b" />
              <Text style={s.stateText}>{t('common.loading')}</Text>
            </View>
          ) : isForbidden ? (
            <View style={s.state} testID="review-queue-forbidden">
              <Text style={s.stateText}>{t('expense.review_forbidden')}</Text>
            </View>
          ) : queue.isError && !queue.data ? (
            <View style={s.state} testID="review-queue-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('review_queue.error')}
              </Text>
              <Pressable
                onPress={() => void queue.refetch()}
                style={({ pressed }) => [
                  s.linkBtn,
                  pressed && s.linkBtnPressed,
                ]}
                accessibilityRole="button"
                testID="review-queue-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.state} testID="review-queue-empty">
              <Text style={s.stateText}>{t('review_queue.empty')}</Text>
            </View>
          )
        }
      />
    </SafeAreaView>
  );
}

/**
 * One triage row. Enriched when the joined expense summary exists;
 * degraded (reasons + waiting-since only) when it doesn't. Both
 * variants link to the expense detail, where Approve/Reject/Edit
 * already live.
 */
function TriageRow({
  item,
  expense,
  jobMap,
}: {
  item: ReviewQueueItem;
  expense: ExpensePublic | undefined;
  jobMap: Map<string, string>;
}) {
  const { t } = useTranslation();
  const detailHref = `/expenses/${item.expense_id}` as unknown as Href;
  const openedDate = formatDateAU(item.opened_at.slice(0, 10));
  const jobName = expense ? jobMap.get(expense.job_id) : undefined;
  const preview = expense
    ? (expense.raw_input_text || expense.description || '').slice(0, 60)
    : null;

  return (
    <Link href={detailHref} asChild>
      <Pressable
        testID={`triage-row-${item.review_id}`}
        accessibilityRole="button"
        accessibilityLabel={t('review_queue.title')}
        hitSlop={4}
        style={({ pressed }) => [s.row, pressed && s.rowPressed]}
      >
        {expense ? (
          <View style={s.rowTop}>
            <Text style={s.amount}>{formatMoney(expense.amount_inc_gst)}</Text>
            <Text style={s.waiting}>
              {t('review_queue.waiting_since', { date: openedDate })}
            </Text>
          </View>
        ) : (
          <View style={s.rowTop}>
            <Text style={s.degraded}>
              {t('review_queue.summary_unavailable')}
            </Text>
            <Text style={s.waiting}>
              {t('review_queue.waiting_since', { date: openedDate })}
            </Text>
          </View>
        )}

        {expense ? (
          <View style={s.rowMid}>
            <Text style={s.date}>{formatDateAU(expense.expense_date)}</Text>
            {jobName ? <Text style={s.dot}> · </Text> : null}
            {jobName ? (
              <Text style={s.job} numberOfLines={1}>
                {jobName}
              </Text>
            ) : null}
          </View>
        ) : null}

        {preview ? (
          <Text style={s.preview} numberOfLines={1}>
            {preview}
          </Text>
        ) : null}

        <View style={s.reasonRow}>
          {item.review_reasons.map((code) => (
            <View key={code} style={s.reasonPill}>
              {/* Audit C-04: defaultValue so a NEW backend reason code
                  degrades to the raw code, not an i18n key string. */}
              <Text style={s.reasonText}>
                {t(`review_reason.${code}`, { defaultValue: code })}
              </Text>
            </View>
          ))}
        </View>
      </Pressable>
    </Link>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
    backgroundColor: tokens.surface,
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
  list: { flex: 1 },
  listContainer: { paddingHorizontal: 16 },
  listEmptyContainer: { flexGrow: 1, justifyContent: 'center' },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  row: {
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: tokens.lineSoft,
    backgroundColor: tokens.surface,
  },
  rowPressed: { backgroundColor: '#f1f5f9' },
  rowTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  amount: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  waiting: { color: '#94a3b8', fontSize: 12 },
  degraded: { color: '#64748b', fontSize: 14, fontStyle: 'italic' },
  rowMid: { flexDirection: 'row', marginTop: 4, alignItems: 'center' },
  date: { color: '#64748b', fontSize: 13 },
  dot: { color: '#94a3b8', fontSize: 13 },
  job: { color: '#64748b', fontSize: 13, flexShrink: 1 },
  preview: { color: '#334155', fontSize: 13, marginTop: 4 },
  reasonRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 6, gap: 6 },
  reasonPill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    backgroundColor: '#fef3c7',
  },
  reasonText: { fontSize: 10, fontWeight: '600', color: '#92400e' },
});
