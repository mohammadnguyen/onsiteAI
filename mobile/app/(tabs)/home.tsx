import { useMemo, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  StyleSheet,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useRouter, type Href } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { useJobs } from '../../src/api/hooks/useJobs';
import {
  useOpenReviewQueue,
  usePendingExpenseSummaries,
} from '../../src/api/hooks/useReviewQueue';
import {
  useExpensesSince,
  type ExpensePublic,
} from '../../src/api/hooks/useExpenses';
import { formatDateAU } from '../../src/util/dates';
import { StatusBadge } from '../../src/ui/kit';
import {
  BriefcaseIcon,
  ClockIcon,
  DollarIcon,
  GearIcon,
} from '../../src/ui/icons';
import { useMe } from '../../src/api/hooks/useAuth';
import { formatMoney } from '../../src/util/format';
import { monthStart, todayISO } from '../../src/util/dates';
import { useScaledStyles } from '../../src/ui/type';
import { tokens } from '../../src/ui/tokens';

/**
 * UI-kit v2 Batch 2: the Home tab.
 *
 * IA decision (operator): TabBar = Home · Jobs · central ➕ · Labour;
 * capture opens via the ➕ (app/capture.tsx, pushed); Settings moved
 * off the tab bar INTO Home (entry row below, pushing /settings).
 *
 * Content v1: the admin stats cards (moved verbatim from the Jobs tab
 * header — same queries, same four-state stat rendering, same
 * navigation targets) + entry rows. Contributors get a money-free
 * Home: title + entries only (the stats component never mounts, so
 * its admin-flavoured queries never fire for contributors — same
 * containment the Jobs tab used).
 */

type StatState = 'loading' | 'value' | 'stale' | 'error';
function statState(isError: boolean, hasData: boolean): StatState {
  if (hasData) return isError ? 'stale' : 'value';
  return isError ? 'error' : 'loading';
}

export default function HomeScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  const qc = useQueryClient();
  const jobs = useJobs();

  // Pull-to-refresh (house pattern: explicit user-pulled flag).
  const [userRefreshing, setUserRefreshing] = useState(false);
  const onRefresh = () => {
    setUserRefreshing(true);
    void Promise.allSettled([
      jobs.refetch(),
      me.refetch(),
      qc.invalidateQueries({ queryKey: ['expenses'] }),
      qc.invalidateQueries({ queryKey: ['review-queue'] }),
    ]).finally(() => setUserRefreshing(false));
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <ScrollView
        contentContainerStyle={s.scroll}
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor="#1e293b"
          />
        }
      >
        {/* Preview-parity: Settings behind the top-right gear (was an
            entry row). */}
        <View style={s.titleRow}>
          <Text style={s.title}>{t('tabs.home')}</Text>
          <Pressable
            style={({ pressed }) => [s.gearBtn, pressed && s.gearPressed]}
            onPress={() => router.push('/settings' as unknown as Href)}
            accessibilityRole="button"
            accessibilityLabel={t('tabs.settings')}
            hitSlop={8}
            testID="home-settings-entry"
          >
            <GearIcon size={20} color={tokens.ink2} />
          </Pressable>
        </View>
        {isAdmin ? <AdminStats /> : null}
        {isAdmin ? <PendingReviewSection /> : null}
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * Preview-parity (annotation ①): the open review queue's first rows
 * live ON the Home screen — the operator clears ~daily, so the list
 * beats a bare count card. Same client-side join as /review-queue
 * (queue rows ⋈ pending summaries); first 3 rows + "View all".
 * Renders nothing while loading/erroring/empty — the stats card above
 * still carries the count in those states.
 */
const HOME_PENDING_LIMIT = 3;

function PendingReviewSection() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const queue = useOpenReviewQueue();
  const summaries = usePendingExpenseSummaries();
  const jobs = useJobs();

  const jobName = (id: string) =>
    jobs.data?.find((j) => j.job_id === id)?.job_name ?? '';

  const expenseById = useMemo(() => {
    const m = new Map<string, ExpensePublic>();
    summaries.data?.items.forEach((e) => m.set(e.expense_id, e));
    return m;
  }, [summaries.data]);

  const rows = useMemo(
    () =>
      (queue.data ?? [])
        .map((q) => expenseById.get(q.expense_id))
        .filter((e): e is ExpensePublic => !!e)
        .slice(0, HOME_PENDING_LIMIT),
    [queue.data, expenseById],
  );

  if (rows.length === 0) return null;

  return (
    <View testID="home-pending-section">
      <View style={s.sectionRow}>
        <Text style={s.sectionTitle}>{t('dashboard.pending_review')}</Text>
        <Pressable
          onPress={() => router.push('/review-queue' as unknown as Href)}
          accessibilityRole="button"
          hitSlop={8}
          testID="home-pending-view-all"
        >
          <Text style={s.viewAll}>{t('home.view_all')}</Text>
        </Pressable>
      </View>
      <View style={s.pendingCard}>
        {rows.map((e, i) => (
          <Pressable
            key={e.expense_id}
            onPress={() =>
              router.push(`/expenses/${e.expense_id}` as unknown as Href)
            }
            accessibilityRole="button"
            style={({ pressed }) => [
              s.pendingRow,
              i > 0 && s.pendingRowBorder,
              pressed && s.entryPressed,
            ]}
            testID={`home-pending-${e.expense_id}`}
          >
            <View style={s.pendingMain}>
              <Text style={s.pendingAmount}>
                {formatMoney(e.amount_inc_gst)}
              </Text>
              <Text style={s.pendingDesc} numberOfLines={1}>
                {e.raw_input_text || e.description || '—'}
              </Text>
              <Text style={s.pendingMeta} numberOfLines={1}>
                {[jobName(e.job_id), formatDateAU(e.expense_date)]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            </View>
            <StatusBadge
              status={e.review_status}
              label={t(`expense.status_${e.review_status}`, {
                defaultValue: e.review_status,
              })}
            />
            <Text style={s.entryChevron}>{'›'}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

/**
 * Admin stats cards — moved VERBATIM from the Jobs tab (O2-B merged
 * dashboard). Queries mount only when this component renders, i.e.
 * admins only. Four-state stat rendering (R1): a failed fetch never
 * coalesces to a real-looking value.
 */
function AdminStats() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const jobs = useJobs();
  const queue = useOpenReviewQueue();
  // Month-to-date window, recomputed per render so a month rollover
  // picks up the new window on the next refresh (ex-Dashboard pattern).
  const monthExpenses = useExpensesSince(monthStart(todayISO()));

  const activeCount = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active').length,
    [jobs.data],
  );
  const monthTotalExGst = useMemo(() => {
    const items = monthExpenses.data?.items ?? [];
    return items
      .filter((e) => e.review_status !== 'rejected')
      .reduce((acc, e) => acc + parseFloat(e.amount_ex_gst ?? '0'), 0);
  }, [monthExpenses.data]);
  const pendingCount = queue.data?.length ?? null;

  const monthStat = statState(
    monthExpenses.isError,
    monthExpenses.data !== undefined,
  );
  const jobsStat = statState(jobs.isError, jobs.data !== undefined);
  const queueStat = statState(queue.isError, queue.data !== undefined);

  return (
    <View style={s.statRow} testID="home-stats">
      <Pressable
        style={({ pressed }) => [s.statCard, pressed && s.statCardPressed]}
        onPress={() => router.push('/expenses/list' as unknown as Href)}
        accessibilityRole="button"
        testID="home-stat-month-spend"
      >
        <View style={[s.statIcon, { backgroundColor: tokens.okBg }]}>
          <DollarIcon size={14} color={tokens.ok} />
        </View>
        <Text style={s.statLabel}>{t('dashboard.month_spend')}</Text>
        <Text
          style={[s.statValue, monthStat === 'error' && s.statValueError]}
          numberOfLines={1}
        >
          {monthStat === 'loading'
            ? '…'
            : monthStat === 'error'
              ? '—'
              : formatMoney(monthTotalExGst.toFixed(2))}
        </Text>
        {monthStat === 'stale' ? (
          <Text style={s.statTag}>{t('dashboard.stale')}</Text>
        ) : null}
      </Pressable>
      <Pressable
        style={({ pressed }) => [
          s.statCard,
          s.statCardPending,
          pressed && s.statCardPressed,
        ]}
        onPress={() => router.push('/review-queue' as unknown as Href)}
        accessibilityRole="button"
        testID="home-stat-pending"
      >
        <View style={[s.statIcon, { backgroundColor: '#ffffff' }]}>
          <ClockIcon size={14} color={tokens.warnFill} />
        </View>
        <Text style={s.statLabel}>{t('dashboard.pending_review')}</Text>
        <Text
          style={[
            s.statValue,
            s.statValuePending,
            queueStat === 'error' && s.statValueError,
          ]}
        >
          {queueStat === 'loading'
            ? '…'
            : queueStat === 'error'
              ? '—'
              : `${pendingCount} ›`}
        </Text>
        {queueStat === 'stale' ? (
          <Text style={s.statTag}>{t('dashboard.stale')}</Text>
        ) : null}
      </Pressable>
      <View style={s.statCard} testID="home-stat-active">
        <View style={[s.statIcon, { backgroundColor: tokens.sel }]}>
          <BriefcaseIcon size={14} color={tokens.selText} />
        </View>
        <Text style={s.statLabel}>{t('dashboard.active_jobs')}</Text>
        <Text style={[s.statValue, jobsStat === 'error' && s.statValueError]}>
          {jobsStat === 'loading'
            ? '…'
            : jobsStat === 'error'
              ? '—'
              : activeCount}
        </Text>
        {jobsStat === 'stale' ? (
          <Text style={s.statTag}>{t('dashboard.stale')}</Text>
        ) : null}
      </View>
    </View>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  scroll: { padding: 16, paddingBottom: 24 },
  title: { fontSize: 22, fontWeight: '600', color: tokens.ink },
  statRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 16,
  },
  statIcon: {
    width: 26,
    height: 26,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 5,
  },
  statCard: {
    flex: 1,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: '#ffffff',
  },
  statCardPending: { backgroundColor: tokens.warnBg, borderColor: tokens.warnBorder },
  statCardPressed: { opacity: 0.7 },
  statLabel: { fontSize: 11, color: tokens.ink2 },
  statValue: {
    fontSize: 17,
    fontWeight: '600',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
  },
  statValuePending: { color: '#92400e' },
  statValueError: { color: tokens.ink3 },
  statTag: { fontSize: 10, color: tokens.warn },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  gearBtn: {
    minWidth: 40,
    minHeight: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: tokens.lineSoft,
  },
  gearPressed: { opacity: 0.6 },
  sectionRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: tokens.ink },
  viewAll: { fontSize: 13, fontWeight: '600', color: tokens.primary },
  pendingCard: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    overflow: 'hidden',
    backgroundColor: '#ffffff',
  },
  pendingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  pendingRowBorder: { borderTopWidth: 1, borderTopColor: tokens.lineSoft },
  pendingMain: { flex: 1, minWidth: 0 },
  pendingAmount: {
    fontSize: 15.5,
    fontWeight: '700',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
  },
  pendingDesc: { fontSize: 13, color: tokens.ink2, marginTop: 2 },
  pendingMeta: { fontSize: 12, color: tokens.ink3, marginTop: 2 },
  entryPressed: { backgroundColor: tokens.lineSoft },
  entryChevron: { fontSize: 20, color: tokens.ink3 },
});
