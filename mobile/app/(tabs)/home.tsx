import { useMemo, useRef, useState } from 'react';
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
import { useExpensesSince } from '../../src/api/hooks/useExpenses';
import { ForeyLogo, GearIcon, PlusIcon } from '../../src/ui/icons';
import { useMe } from '../../src/api/hooks/useAuth';
import { formatMoney } from '../../src/util/format';
import { monthStart, todayISO, formatTodayLine } from '../../src/util/dates';
import { useScaledStyles } from '../../src/ui/type';
import { tokens } from '../../src/ui/tokens';
import { ReviewCardStack } from '../../src/components/home/ReviewCardStack';
import { TodayAttendance } from '../../src/components/home/TodayAttendance';
import { Toast } from '../../src/ui/kit';

/**
 * forey F1 — the「今天」page (handoff §2). Replaces the v2 Home.
 *
 * Shape: brand header + date line → capture entry card → stat duo →
 * in-place review card stack → today's attendance.
 *
 * The stat row is TWO cards (1.25 : 1), not the v2's three: the third
 * card is what squeezed the month total into "$45,76…" on Build 29.
 * Active-job count moves into the date line, where it reads better
 * anyway.
 *
 * Money gating unchanged: contributors get a money-free Today (capture
 * entry + attendance); the stats and the review stack never mount for
 * them, so their admin-flavoured queries never fire — the same
 * containment the v2 Home used. The server strips regardless.
 */

type StatState = 'loading' | 'value' | 'stale' | 'error';
function statState(isError: boolean, hasData: boolean): StatState {
  if (hasData) return isError ? 'stale' : 'value';
  return isError ? 'error' : 'loading';
}

export default function HomeScreen() {
  const s = useScaledStyles(base);
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  const qc = useQueryClient();
  const jobs = useJobs();
  // seq: two identical toasts in a row (approve $500 twice) must both
  // fire — the Toast effect keys on it, not on the string.
  const [toast, setToast] = useState<{ text: string; seq: number } | null>(
    null,
  );
  const toastSeq = useRef(0);
  const showToast = (text: string) => {
    toastSeq.current += 1;
    setToast({ text, seq: toastSeq.current });
  };
  // Optimistically-cleared review ids. Owned HERE, not in the stack,
  // so the pending card's count + total move with the card the user
  // just cleared instead of lagging until the refetch lands.
  const [dequeued, setDequeued] = useState<string[]>([]);

  const activeCount = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active').length,
    [jobs.data],
  );
  // R1 four-state rule: a failed/loading jobs query must never render a
  // countable "0 active projects". Moving this count out of its stat
  // card lost the guard the card had — the date line drops the count
  // entirely until the data is real.
  const jobsStat = statState(jobs.isError, jobs.data !== undefined);
  const showCount = jobsStat === 'value' || jobsStat === 'stale';

  const [userRefreshing, setUserRefreshing] = useState(false);
  const onRefresh = () => {
    setUserRefreshing(true);
    void Promise.allSettled([
      jobs.refetch(),
      me.refetch(),
      qc.invalidateQueries({ queryKey: ['expenses'] }),
      qc.invalidateQueries({ queryKey: ['review-queue'] }),
      qc.invalidateQueries({ queryKey: ['labour-entries'] }),
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
            tintColor={tokens.ink3}
          />
        }
      >
        <View style={s.titleRow}>
          <View style={s.brandRow}>
            <ForeyLogo size={30} />
            <Text style={s.title}>{t('tabs.home')}</Text>
          </View>
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
        <Text style={s.dateLine} testID="home-date-line">
          {showCount
            ? t('home.date_line', {
                date: formatTodayLine(todayISO(), i18n.language),
                count: activeCount,
              })
            : formatTodayLine(todayISO(), i18n.language)}
        </Text>

        <CaptureEntryCard />
        {isAdmin ? <StatDuo dequeued={dequeued} /> : null}
        {isAdmin ? (
          <ReviewCardStack
            dequeued={dequeued}
            onDequeue={(id) => setDequeued((prev) => [...prev, id])}
            onRestore={(id) =>
              setDequeued((prev) => prev.filter((x) => x !== id))
            }
            onToast={showToast}
          />
        ) : null}
        <TodayAttendance />
      </ScrollView>
      <Toast
        text={toast?.text ?? null}
        seq={toast?.seq ?? 0}
        onDone={() => setToast(null)}
      />
    </SafeAreaView>
  );
}

/** Handoff §2 记一笔入口: the page's own doorway into capture — the
 *  tab bar's ➕ does the same, and this states what to type. */
function CaptureEntryCard() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Pressable
      style={({ pressed }) => [s.captureCard, pressed && s.capturePressed]}
      onPress={() => router.push('/capture' as unknown as Href)}
      accessibilityRole="button"
      accessibilityLabel={t('capture.title')}
      testID="home-capture-entry"
    >
      <View style={s.captureSquare}>
        <PlusIcon size={18} color="#ffffff" />
      </View>
      <Text style={s.capturePlaceholder} numberOfLines={1}>
        {t('home.capture_placeholder')}
      </Text>
      {/* The design shows a mic here. There is no speech-to-text in the
          app, and a mic that opens a keyboard would be a lie — omitted
          until voice capture is a real slice. */}
    </Pressable>
  );
}

/** Handoff §2 统计行: month spend (1.25) + pending (1). Pending turns
 *  green the moment the queue is clear. */
function StatDuo({ dequeued }: { dequeued: string[] }) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const queue = useOpenReviewQueue();
  const summaries = usePendingExpenseSummaries();
  const monthExpenses = useExpensesSince(monthStart(todayISO()));

  const monthTotalExGst = useMemo(() => {
    const items = monthExpenses.data?.items ?? [];
    return items
      .filter((e) => e.review_status !== 'rejected')
      .reduce((acc, e) => acc + parseFloat(e.amount_ex_gst ?? '0'), 0);
  }, [monthExpenses.data]);
  // The query pulls ONE 500-row page. Past that the total is partial —
  // say so with the app's existing "incomplete amount" convention (an
  // amber +) rather than presenting a short number as the month's spend.
  const monthPartial = monthExpenses.data?.next_cursor != null;

  // Pending total is a CASH figure (inc-GST, as captured) — it answers
  // "how much money is sitting unreviewed", not a cost basis.
  const openRows = useMemo(
    () => (queue.data ?? []).filter((q) => !dequeued.includes(q.review_id)),
    [queue.data, dequeued],
  );
  const pendingTotal = useMemo(() => {
    const open = new Set(openRows.map((q) => q.expense_id));
    return (summaries.data?.items ?? [])
      .filter((e) => open.has(e.expense_id))
      .reduce((acc, e) => acc + parseFloat(e.amount_inc_gst ?? '0'), 0);
  }, [openRows, summaries.data]);

  const pendingCount = openRows.length;
  // The total comes from the SUMMARIES query — it needs its own
  // evidence. Gated on queueStat alone it rendered a confident "$0.00"
  // beside a non-zero count whenever summaries failed.
  const totalStat = statState(
    summaries.isError,
    summaries.data !== undefined,
  );
  const monthStat = statState(
    monthExpenses.isError,
    monthExpenses.data !== undefined,
  );
  const queueStat = statState(queue.isError, queue.data !== undefined);
  const clear = queueStat === 'value' && pendingCount === 0;

  return (
    <View style={s.statRow} testID="home-stats">
      <Pressable
        style={({ pressed }) => [s.statCard, s.statCardWide, pressed && s.statPressed]}
        onPress={() => router.push('/expenses/list' as unknown as Href)}
        accessibilityRole="button"
        testID="home-stat-month-spend"
      >
        <Text style={s.statLabel}>{t('home.month_spend')}</Text>
        <Text
          style={[s.statValue, monthStat === 'error' && s.statValueError]}
          numberOfLines={1}
          // Money shrinks to fit; it never clips (Build 29 truncated a
          // 6-figure total to "$45,76…").
          adjustsFontSizeToFit
          minimumFontScale={0.7}
        >
          {monthStat === 'loading'
            ? '…'
            : monthStat === 'error'
              ? '—'
              : formatMoney(monthTotalExGst.toFixed(2))}
          {monthStat !== 'loading' && monthStat !== 'error' && monthPartial ? (
            <Text style={s.partialPlus}>+</Text>
          ) : null}
        </Text>
        {monthStat === 'stale' ? (
          <Text style={s.statTag}>{t('dashboard.stale')}</Text>
        ) : null}
      </Pressable>

      <Pressable
        style={({ pressed }) => [
          s.statCard,
          clear ? s.statCardClear : s.statCardPending,
          pressed && s.statPressed,
        ]}
        onPress={() => router.push('/review-queue' as unknown as Href)}
        accessibilityRole="button"
        testID="home-stat-pending"
      >
        <Text
          style={[s.statLabel, clear ? s.statLabelClear : s.statLabelPending]}
          numberOfLines={1}
        >
          {t('dashboard.pending_review')}
        </Text>
        <Text
          style={[
            s.statValue,
            clear ? s.statValueClear : s.statValuePending,
            queueStat === 'error' && s.statValueError,
          ]}
          numberOfLines={1}
          adjustsFontSizeToFit
          minimumFontScale={0.7}
        >
          {queueStat === 'loading'
            ? '…'
            : queueStat === 'error'
              ? '—'
              : clear
                ? t('home.cleared')
                : t('home.items', { count: pendingCount })}
        </Text>
        {!clear && queueStat === 'value' && totalStat === 'value' ? (
          <Text
            style={s.statSub}
            numberOfLines={1}
            // The only money Text here that lacked shrink-to-fit: the
            // narrow card gives it ~129pt and zh at xlarge needs ~154.
            adjustsFontSizeToFit
            minimumFontScale={0.7}
          >
            {t('home.pending_total', {
              sum: formatMoney(pendingTotal.toFixed(2)),
            })}
          </Text>
        ) : null}
        {queueStat === 'stale' ? (
          <Text style={s.statTag}>{t('dashboard.stale')}</Text>
        ) : null}
      </Pressable>
    </View>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  scroll: { padding: 16, paddingBottom: 28, gap: 16 },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  title: {
    fontSize: 26,
    fontWeight: '800',
    color: tokens.ink,
    letterSpacing: -0.4,
  },
  dateLine: { fontSize: 13, color: tokens.ink3, marginTop: -10 },
  gearBtn: {
    minWidth: 40,
    minHeight: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
  },
  gearPressed: { opacity: 0.6 },

  captureCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
    padding: 12,
    shadowColor: '#101828',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.05,
    shadowRadius: 16,
    elevation: 2,
  },
  capturePressed: { opacity: 0.8 },
  captureSquare: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: tokens.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  capturePlaceholder: { flex: 1, fontSize: 14.5, color: tokens.muted },

  statRow: { flexDirection: 'row', gap: 10 },
  statCard: {
    flex: 1,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 11,
    backgroundColor: tokens.surface,
    gap: 4,
  },
  statCardWide: { flex: 1.25 },
  statCardPending: {
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
  },
  statCardClear: { backgroundColor: tokens.okBg, borderColor: tokens.okBorder },
  statPressed: { opacity: 0.75 },
  statLabel: { fontSize: 11, color: tokens.ink2 },
  statSub: { fontSize: 11, color: tokens.warn, fontVariant: ['tabular-nums'] },
  partialPlus: { color: tokens.warnFill, fontWeight: '800' },
  statLabelPending: { color: tokens.warn },
  statLabelClear: { color: tokens.ok },
  statValue: {
    fontSize: 23,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.3,
  },
  statValuePending: { color: tokens.warnMid },
  statValueClear: { color: tokens.ok },
  statValueError: { color: tokens.ink3 },
  statTag: { fontSize: 10, color: tokens.warn },
});
