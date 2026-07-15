import { useMemo, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  ActivityIndicator,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { BudgetBar, StatusBadge } from '../../src/ui/kit';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useJobs, type JobPublic } from '../../src/api/hooks/useJobs';
import { useRouter, type Href } from 'expo-router';
import { useMe } from '../../src/api/hooks/useAuth';
import { NewJobModal } from '../../src/components/NewJobModal';
import { formatMoney } from '../../src/util/format';
import { localizeJobStatus } from '../../src/util/jobStatus';
import { useScaledStyles } from '../../src/ui/type';

/**
 * O2-B (Dashboard→Jobs merge): the retired Dashboard tab's banding +
 * ranking logic, carried verbatim so the admin Jobs list preserves the
 * red/amber/green at-a-glance value. Banding uses the SERVER-computed
 * thresholds on each `JobSummary` row; contributors receive
 * `summary=null` (server-stripped), so every contributor row bands to
 * 'none' and their list stays money-free by construction.
 */
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
// UI-kit v2: band → data-status tone (bar colours now come from the
// kit's toneFill; banding logic + per-job thresholds unchanged).
const BAND_TONE = { red: 'bad', amber: 'warn', green: 'ok' } as const;

/** Budget-pressure ordering (ex-Dashboard): red → amber → green by
 * consumed % desc, then budget-less jobs by spend desc. */
function rankByPressure(jobs: JobPublic[]): JobPublic[] {
  return [...jobs].sort((a, b) => {
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
}


export default function JobsScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const jobsQuery = useJobs();
  const { data, isLoading, isError } = jobsQuery;
  // X-2: pull-to-refresh (house pattern — explicit "user pulled" flag
  // so background refetches never pin the spinner). B2: the stats
  // cards moved to Home, so this pull only refreshes the jobs list —
  // Home has its own pull that covers the stats roots.
  const [userRefreshing, setUserRefreshing] = useState(false);
  const onRefresh = () => {
    setUserRefreshing(true);
    void jobsQuery.refetch().finally(() => setUserRefreshing(false));
  };
  // O2-B: role drives the merged-dashboard surfaces. VISIBILITY ONLY —
  // the backend strips job money for contributors regardless (jobs
  // money strip), and the admin-only queries live inside
  // AdminStatsHeader so they never fire for contributors.
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  const router = useRouter();
  // B4-1: job details is a pushed PAGE now (app/jobs/[id]/index.tsx).
  // The whole native-Modal apparatus — selectedJob store, focus gate,
  // modalEpoch re-present — is retired: back() from the page (and from
  // expense drill-downs beyond it) works natively under the root Stack.
  const [showNewJob, setShowNewJob] = useState(false);

  const jobs = useMemo(() => data ?? [], [data]);

  // M5: active jobs first; archived (status=completed) jobs sit
  // below a labelled divider with muted styling — still visible and
  // tappable for history, out of the way for daily use.
  // O2-B: for ADMINS the active slice is ordered by budget pressure
  // (worst first — the retired Dashboard's ranking). Contributors keep
  // the plain order (their rows carry no summary anyway).
  const listData = useMemo<JobListItem[]>(() => {
    const activeRaw = jobs.filter((j) => j.status === 'active');
    const active = isAdmin ? rankByPressure(activeRaw) : activeRaw;
    const archived = jobs.filter((j) => j.status !== 'active');
    const out: JobListItem[] = active.map((job) => ({ kind: 'job', job }));
    if (archived.length > 0) {
      out.push({ kind: 'archived-header' });
      archived.forEach((job) => out.push({ kind: 'job', job }));
    }
    return out;
  }, [jobs, isAdmin]);

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
      <View style={s.header}>
        <Text style={s.title}>{t('jobs.title')}</Text>
        <TouchableOpacity
          onPress={() => setShowNewJob(true)}
          style={s.newBtn}
          testID="job-new-btn"
          accessibilityRole="button"
          accessibilityLabel={t('jobs.new')}
        >
          <Text style={s.newBtnText}>{t('jobs.new')}</Text>
        </TouchableOpacity>
      </View>
      {/* UI-kit v2 B2: the admin stats cards moved to the Home tab
          (app/(tabs)/home.tsx) with the IA rework. */}
      {isLoading ? (
        <View style={s.center}>
          <ActivityIndicator size="large" color="#1e293b" />
          <Text style={s.loadingText}>{t('jobs.loading')}</Text>
        </View>
      ) : isError && !data ? (
        // X-2 follow-up: blank to an error ONLY with no cached list —
        // a failed focus/pull refetch keeps showing the cached jobs.
        <View style={s.center}>
          <Text style={s.errText}>{t('common.error')}</Text>
        </View>
      ) : jobs.length === 0 ? (
        <View style={s.center}>
          <Text style={s.emptyText}>{t('jobs.empty')}</Text>
        </View>
      ) : (
        <FlatList
          data={listData}
          keyExtractor={(item) =>
            item.kind === 'job' ? item.job.job_id : '__archived_header__'
          }
          renderItem={({ item }) =>
            item.kind === 'job' ? (
              <JobRow
                job={item.job}
                archived={item.job.status !== 'active'}
                pressure={isAdmin}
                onPress={() =>
                  // navigate, not push: expo-router v6 REUSES the top
                  // route when the name matches, so a double-tap can't
                  // stack two detail pages (forward-edge twin of the
                  // useOneShotBack guard).
                  router.navigate(
                    `/jobs/${item.job.job_id}` as unknown as Href,
                  )
                }
              />
            ) : (
              <Text style={s.archivedHeader} testID="jobs-archived-header">
                {t('job.archived_section')}
              </Text>
            )
          }
          ItemSeparatorComponent={() => <View style={s.sep} />}
          contentContainerStyle={s.listContent}
          refreshControl={
            <RefreshControl
              refreshing={userRefreshing}
              onRefresh={onRefresh}
              tintColor="#1e293b"
            />
          }
        />
      )}
      <NewJobModal
        visible={showNewJob}
        onClose={() => setShowNewJob(false)}
      />
    </SafeAreaView>
  );
}

// M5: jobs-list item — either a job row or the single archived-section
// divider injected between active and archived groups.
type JobListItem = { kind: 'job'; job: JobPublic } | { kind: 'archived-header' };

function JobRow({
  job,
  archived,
  pressure,
  onPress,
}: {
  job: JobPublic;
  archived?: boolean;
  /** O2-B: admin rows render the budget-pressure bar + remaining line
   * (ex-Dashboard). Contributor rows never do — and their summary is
   * server-stripped to null anyway. */
  pressure?: boolean;
  onPress: () => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const sum = job.summary;
  const band = pressure && !archived ? bandFor(job) : 'none';
  const pct =
    sum?.percent_consumed != null ? parseFloat(sum.percent_consumed) : null;
  const remaining =
    sum?.remaining_ex_gst != null ? parseFloat(sum.remaining_ex_gst) : null;

  return (
    <TouchableOpacity
      onPress={onPress}
      style={[s.row, archived && s.rowArchived]}
      testID={`job-row-${job.job_id}`}
    >
      <View style={s.rowTop}>
        <View style={s.rowMain}>
          <Text style={s.rowName}>{job.job_name}</Text>
          {job.job_code ? <Text style={s.rowCode}>{job.job_code}</Text> : null}
        </View>
        <StatusBadge
          status={job.status}
          label={localizeJobStatus(job.status, t)}
        />
      </View>
      {band !== 'none' ? (
        <View style={s.pressureWrap} testID={`job-pressure-${job.job_id}`}>
          {/* RAW pct on purpose: the label must say "143% used" on an
              overspent job — BudgetBar clamps only the FILL width. */}
          <BudgetBar
            pctUsed={pct ?? 0}
            tone={BAND_TONE[band]}
            leftText={
              remaining != null && remaining < 0
                ? t('dashboard.over_by', {
                    amount: formatMoney(Math.abs(remaining).toFixed(2)),
                  })
                : t('dashboard.left', {
                    amount: formatMoney((remaining ?? 0).toFixed(2)),
                  })
            }
          />
        </View>
      ) : null}
      {/* O2-C (U8): budget-less ACTIVE jobs still get spend context on
          admin rows — data is already on the summary; no extra query. */}
      {pressure && !archived && band === 'none' && sum ? (
        <Text style={s.metricHint} testID={`job-spent-${job.job_id}`}>
          {t('dashboard.spent_label', {
            spent: formatMoney(sum.actual_ex_gst ?? '0'),
          })}
        </Text>
      ) : null}
    </TouchableOpacity>
  );
}

// B4-1: the job-detail modal and its sections moved to the pushed
// page at app/jobs/[id]/index.tsx. This file is the LIST only.

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  // UI-kit v2 margin hero (values unchanged; layout per design spec)
  header: {
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 8,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: { fontSize: 22, fontWeight: '600', color: '#0f172a' },
  newBtn: {
    backgroundColor: '#1e293b',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
    minHeight: 36,
    justifyContent: 'center',
  },
  newBtnText: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  loadingText: { marginTop: 12, color: '#64748b' },
  emptyText: { color: '#64748b', fontSize: 16 },
  errText: { color: '#b91c1c', fontSize: 16 },
  listContent: { paddingBottom: 24 },
  // O2-B: row is now a column (top line + optional pressure block);
  // rowTop carries the original horizontal name/code/badge layout.
  row: {
    paddingVertical: 14,
    paddingHorizontal: 16,
    backgroundColor: '#ffffff',
    gap: 8,
  },
  rowTop: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  rowMain: { flex: 1 },
  // B2: stats-card styles moved to app/(tabs)/home.tsx with the cards.
  // bar visuals now live in src/ui/kit.tsx (BudgetBar); job status
  // pills in StatusBadge — the old inline styles are removed.
  pressureWrap: { marginTop: 2 },
  metricHint: { fontSize: 12, color: '#64748b', paddingVertical: 2 },
  rowName: { fontSize: 16, color: '#0f172a', fontWeight: '500' },
  rowCode: { fontSize: 13, color: '#64748b', marginTop: 2 },
  sep: { height: 1, backgroundColor: '#e2e8f0' },
  // Apple HIG: tappable target ≥ 44×44pt. Without this, the bare ×
  // glyph is too small to reach with a thumb, especially in the corner.
  // Tier 1B: Edit button in the job detail modal header.
  // Spending section: top-level summary only (total spent / budget /
  // remaining). Per-category breakdown removed per operator dogfood
  // signal — redundant with the per-expense list shown below.
  // M5: lifecycle section + archived-list styling.
  rowArchived: { opacity: 0.6 },
  archivedHeader: {
    paddingHorizontal: 16,
    paddingTop: 18,
    paddingBottom: 6,
    fontSize: 13,
    fontWeight: '600',
    color: '#94a3b8',
    textTransform: 'uppercase',
  },
});
