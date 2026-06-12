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
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { useJobs, type JobPublic } from '../../src/api/hooks/useJobs';
import { useExpensesSince } from '../../src/api/hooks/useExpenses';
import { useOpenReviewQueue } from '../../src/api/hooks/useReviewQueue';
import { useMe } from '../../src/api/hooks/useAuth';
import { useSelectedJobStore } from '../../src/store/selectedJob';
import { formatMoney } from '../../src/util/format';

/**
 * Dashboard v1 (admin-only): the morning-glance screen.
 *
 * Three stat cards — this-month spend (ex GST), pending-review count
 * (tap → M3 triage), active-jobs count — above the jobs ranked by
 * budget pressure. Banding uses the SERVER-computed thresholds
 * shipped on every `JobSummary` row (`effective_warning_amber_pct` /
 * `effective_warning_red_pct`, always populated), so the dashboard
 * agrees with whatever per-job overrides admins set — no client-side
 * threshold policy.
 *
 * Data: everything comes from endpoints that already existed —
 * GET /jobs (per-row summary), GET /review-queue?status=open (M3
 * hook), GET /expenses?from=… (M2-A list). ZERO backend changes.
 *
 * Role behaviour: /auth/me gates which BODY renders. Contributors get
 * the pre-dashboard placeholder (their backend access to budget data
 * is route-composition-only today); the admin body is a separate
 * component so its admin-flavoured queries never fire for
 * contributors. Backend remains authoritative regardless.
 *
 * Tap-throughs: pending card → /review-queue; month card →
 * /expenses/list; job row → Jobs tab with the detail modal opened via
 * the selectedJob store (the same store-driven pattern the Jobs tab
 * already uses for cross-screen modal state).
 */

export default function DashboardScreen() {
  const { t } = useTranslation();
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';

  if (!isAdmin) {
    return (
      <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
        <View style={s.placeholderWrap}>
          <Text style={s.title}>{t('tabs.dashboard')}</Text>
          <Text style={s.placeholderBody}>
            {t('common.unavailable_in_this_version')}
          </Text>
        </View>
      </SafeAreaView>
    );
  }
  return <AdminDashboard />;
}

type Band = 'red' | 'amber' | 'green' | 'none';

function bandFor(job: JobPublic): Band {
  const sum = job.summary;
  if (!sum) return 'none';
  if (sum.percent_consumed == null) return 'none';
  const pct = parseFloat(sum.percent_consumed);
  if (sum.overspend || pct >= parseFloat(sum.effective_warning_red_pct)) {
    return 'red';
  }
  if (pct >= parseFloat(sum.effective_warning_amber_pct)) return 'amber';
  return 'green';
}

const BAND_ORDER: Record<Band, number> = { red: 0, amber: 1, green: 2, none: 3 };
const BAND_COLORS: Record<Exclude<Band, 'none'>, string> = {
  red: '#dc2626',
  amber: '#d97706',
  green: '#16a34a',
};

function isoMonthStart(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  return `${now.getFullYear()}-${m}-01`;
}

function AdminDashboard() {
  const { t } = useTranslation();
  const router = useRouter();
  const setSelectedJobId = useSelectedJobStore((st) => st.setSelectedJobId);

  const jobs = useJobs();
  const queue = useOpenReviewQueue();
  // Recomputed per render so a month rollover picks up the new window
  // on the next refresh (same freshness pattern as the M2-B presets).
  const monthFrom = isoMonthStart();
  const monthExpenses = useExpensesSince(monthFrom);
  const [userRefreshing, setUserRefreshing] = useState(false);

  const activeJobs = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active'),
    [jobs.data],
  );

  // Ranked by budget pressure: red → amber → green (each by consumed
  // % descending), then budget-less jobs by spend descending.
  const rankedJobs = useMemo(() => {
    return [...activeJobs].sort((a, b) => {
      const ba = bandFor(a);
      const bb = bandFor(b);
      if (BAND_ORDER[ba] !== BAND_ORDER[bb]) {
        return BAND_ORDER[ba] - BAND_ORDER[bb];
      }
      if (ba === 'none') {
        return (
          parseFloat(b.summary?.actual_ex_gst ?? '0') -
          parseFloat(a.summary?.actual_ex_gst ?? '0')
        );
      }
      return (
        parseFloat(b.summary?.percent_consumed ?? '0') -
        parseFloat(a.summary?.percent_consumed ?? '0')
      );
    });
  }, [activeJobs]);

  const monthTotalExGst = useMemo(() => {
    const items = monthExpenses.data?.items ?? [];
    return items
      .filter((e) => e.review_status !== 'rejected')
      .reduce((acc, e) => acc + parseFloat(e.amount_ex_gst), 0);
  }, [monthExpenses.data]);

  const pendingCount = queue.data?.length ?? null;

  const onRefresh = () => {
    setUserRefreshing(true);
    void Promise.allSettled([
      jobs.refetch(),
      queue.refetch(),
      monthExpenses.refetch(),
    ]).finally(() => setUserRefreshing(false));
  };

  const openJob = (jobId: string) => {
    // Store-driven modal pattern: the Jobs tab presents its detail
    // modal whenever selectedJobId is set, including on tab focus.
    setSelectedJobId(jobId);
    router.push('/(tabs)/jobs' as unknown as Href);
  };

  const loading = jobs.isLoading;

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <FlatList
        data={loading ? [] : rankedJobs}
        keyExtractor={(j) => j.job_id}
        renderItem={({ item }) => (
          <JobPressureRow job={item} onPress={() => openJob(item.job_id)} />
        )}
        style={s.list}
        contentContainerStyle={s.listContent}
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor="#1e293b"
          />
        }
        testID="dashboard-list"
        ListHeaderComponent={
          <View>
            <Text style={s.title}>{t('tabs.dashboard')}</Text>
            <View style={s.statRow}>
              <Pressable
                style={({ pressed }) => [s.statCard, pressed && s.cardPressed]}
                onPress={() => router.push('/expenses/list' as unknown as Href)}
                accessibilityRole="button"
                testID="dashboard-month-spend"
              >
                <Text style={s.statLabel}>{t('dashboard.month_spend')}</Text>
                <Text style={s.statValue} numberOfLines={1}>
                  {monthExpenses.isLoading ? '…' : formatMoney(monthTotalExGst.toFixed(2))}
                </Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [
                  s.statCard,
                  s.statCardPending,
                  pressed && s.cardPressed,
                ]}
                onPress={() => router.push('/review-queue' as unknown as Href)}
                accessibilityRole="button"
                testID="dashboard-pending"
              >
                <Text style={[s.statLabel, s.statLabelPending]}>
                  {t('dashboard.pending_review')}
                </Text>
                <Text style={[s.statValue, s.statValuePending]}>
                  {pendingCount == null ? '…' : pendingCount}
                  {' ›'}
                </Text>
              </Pressable>
              <View style={s.statCard} testID="dashboard-active-jobs">
                <Text style={s.statLabel}>{t('dashboard.active_jobs')}</Text>
                <Text style={s.statValue}>
                  {jobs.isLoading ? '…' : activeJobs.length}
                </Text>
              </View>
            </View>
            <Text style={s.sectionHeading}>{t('dashboard.jobs_heading')}</Text>
          </View>
        }
        ListEmptyComponent={
          loading ? (
            <View style={s.state} testID="dashboard-loading">
              <ActivityIndicator color="#1e293b" />
              <Text style={s.stateText}>{t('common.loading')}</Text>
            </View>
          ) : jobs.isError ? (
            <View style={s.state} testID="dashboard-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('dashboard.error')}
              </Text>
              <Pressable
                onPress={() => void jobs.refetch()}
                style={({ pressed }) => [
                  s.linkBtn,
                  pressed && s.linkBtnPressed,
                ]}
                accessibilityRole="button"
                testID="dashboard-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.state} testID="dashboard-empty">
              <Text style={s.stateText}>{t('dashboard.empty')}</Text>
            </View>
          )
        }
      />
    </SafeAreaView>
  );
}

function JobPressureRow({
  job,
  onPress,
}: {
  job: JobPublic;
  onPress: () => void;
}) {
  const { t } = useTranslation();
  const sum = job.summary;
  const band = bandFor(job);
  const pct =
    sum?.percent_consumed != null ? parseFloat(sum.percent_consumed) : null;
  const barWidth = pct != null ? Math.min(pct, 100) : 0;
  const remaining =
    sum?.remaining_ex_gst != null ? parseFloat(sum.remaining_ex_gst) : null;

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={job.job_name}
      testID={`dashboard-job-${job.job_id}`}
      style={({ pressed }) => [s.jobCard, pressed && s.cardPressed]}
    >
      <View style={s.jobTop}>
        <Text style={s.jobName} numberOfLines={1}>
          {job.job_name}
        </Text>
        {band === 'none' ? (
          <Text style={s.jobNoBudget}>{t('dashboard.no_budget')}</Text>
        ) : remaining != null && remaining < 0 ? (
          <Text style={[s.jobDelta, s.deltaRed]}>
            {t('dashboard.over_by', {
              amount: formatMoney(Math.abs(remaining).toFixed(2)),
            })}
          </Text>
        ) : (
          <Text
            style={[
              s.jobDelta,
              band === 'amber' ? s.deltaAmber : s.deltaGreen,
            ]}
          >
            {t('dashboard.left', {
              amount: formatMoney((remaining ?? 0).toFixed(2)),
            })}
          </Text>
        )}
      </View>
      {band !== 'none' ? (
        <>
          <View style={s.barTrack}>
            <View
              style={[
                s.barFill,
                { width: `${barWidth}%`, backgroundColor: BAND_COLORS[band] },
              ]}
            />
          </View>
          <Text style={s.jobSub}>
            {t('dashboard.spent_of', {
              spent: formatMoney(sum?.actual_ex_gst ?? '0'),
              budget: formatMoney(sum?.total_budget_ex_gst ?? '0'),
            })}
          </Text>
        </>
      ) : (
        <Text style={s.jobSub}>
          {t('dashboard.spent_label', {
            spent: formatMoney(sum?.actual_ex_gst ?? '0'),
          })}
        </Text>
      )}
    </Pressable>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  placeholderWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  placeholderBody: {
    color: '#475569',
    fontSize: 16,
    textAlign: 'center',
    lineHeight: 22,
  },
  list: { flex: 1 },
  listContent: { padding: 16, paddingBottom: 32 },
  title: {
    fontSize: 22,
    fontWeight: '600',
    color: '#0f172a',
    marginBottom: 14,
  },
  statRow: { flexDirection: 'row', gap: 8, marginBottom: 18 },
  statCard: {
    flex: 1,
    backgroundColor: '#f8fafc',
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 10,
  },
  statCardPending: { backgroundColor: '#fef3c7' },
  cardPressed: { opacity: 0.7 },
  statLabel: { fontSize: 11, color: '#64748b', marginBottom: 4 },
  statLabelPending: { color: '#92400e' },
  statValue: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  statValuePending: { color: '#78350f' },
  sectionHeading: {
    fontSize: 14,
    fontWeight: '600',
    color: '#64748b',
    marginBottom: 8,
  },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  jobCard: {
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  jobTop: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 8,
  },
  jobName: {
    fontSize: 15,
    fontWeight: '600',
    color: '#0f172a',
    flexShrink: 1,
  },
  jobDelta: { fontSize: 12, fontWeight: '600' },
  deltaRed: { color: '#b91c1c' },
  deltaAmber: { color: '#b45309' },
  deltaGreen: { color: '#15803d' },
  jobNoBudget: { fontSize: 12, color: '#94a3b8' },
  barTrack: {
    height: 6,
    backgroundColor: '#f1f5f9',
    borderRadius: 3,
    marginTop: 8,
    marginBottom: 4,
    overflow: 'hidden',
  },
  barFill: { height: 6, borderRadius: 3 },
  jobSub: { fontSize: 11, color: '#64748b', marginTop: 2 },
});
