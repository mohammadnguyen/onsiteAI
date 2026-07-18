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
import { StatusBadge } from '../../src/ui/kit';
import { tokens, toneFill, toneText } from '../../src/ui/tokens';
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
  // Operator 2026-07-18: the list shows ONLY active jobs by default;
  // archived live behind their own top filter — never mixed in.
  const [statusFilter, setStatusFilter] = useState<'active' | 'archived'>(
    'active',
  );
  const activeCount = useMemo(
    () => jobs.filter((j) => j.status === 'active').length,
    [jobs],
  );
  const archivedCount = jobs.length - activeCount;
  const listData = useMemo<JobListItem[]>(() => {
    if (statusFilter === 'archived') {
      const archived = jobs.filter((j) => j.status !== 'active');
      const out: JobListItem[] = [];
      if (archived.length > 0) out.push({ kind: 'archived-header' });
      archived.forEach((job) => out.push({ kind: 'job', job }));
      return out;
    }
    const activeRaw = jobs.filter((j) => j.status === 'active');
    const active = isAdmin ? rankByPressure(activeRaw) : activeRaw;
    return active.map((job) => ({ kind: 'job', job }));
  }, [jobs, isAdmin, statusFilter]);

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
      {/* Spec §7 filter row: 进行中 N / 已归档 N — selected = black. */}
      <View style={s.filterRow} testID="jobs-filter-row">
        {(
          [
            ['active', t('jobs.filter_active', { count: activeCount })],
            ['archived', t('jobs.filter_archived', { count: archivedCount })],
          ] as const
        ).map(([key, label]) => (
          <TouchableOpacity
            key={key}
            style={[s.filterPill, statusFilter === key && s.filterPillOn]}
            onPress={() => setStatusFilter(key)}
            accessibilityRole="radio"
            accessibilityState={{ selected: statusFilter === key }}
            testID={`jobs-filter-${key}`}
          >
            <Text
              style={[
                s.filterPillText,
                statusFilter === key && s.filterPillTextOn,
              ]}
            >
              {label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
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
      ) : listData.length === 0 ? (
        <View style={s.center}>
          <Text style={s.emptyText}>
            {statusFilter === 'archived'
              ? t('jobs.no_archived')
              : t('jobs.empty')}
          </Text>
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
              <ArchivedHeader jobs={jobs} isAdmin={isAdmin} />
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

/**
 * F4 (handoff §7 已归档区): realised net profit for an ARCHIVED job —
 * contract (ex-GST) − actual cost (ex-GST). Display arithmetic on two
 * server figures (same precedent as margin-to-date); null when either
 * side is missing (no contract set, or contributor-stripped summary).
 */
function netProfit(job: JobPublic): number | null {
  if (job.contract_value_ex_gst == null || job.summary?.actual_ex_gst == null)
    return null;
  const contract = parseFloat(job.contract_value_ex_gst);
  const actual = parseFloat(job.summary.actual_ex_gst);
  if (!Number.isFinite(contract) || !Number.isFinite(actual)) return null;
  return contract - actual;
}

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
          {/* F4 (operator decision): subline = SITE ADDRESS, code as
              fallback. */}
          {job.site_address || job.job_code ? (
            <Text style={s.rowCode} numberOfLines={1}>
              {job.site_address || job.job_code}
            </Text>
          ) : null}
        </View>
        {/* Fidelity §7: active budget cards carry 已用N% top-right in
            the band colour; archived cards keep the status badge. */}
        {band !== 'none' && pct != null ? (
          <Text
            style={[s.pctTop, { color: toneText[BAND_TONE[band]] }]}
            testID={`job-pct-${job.job_id}`}
          >
            {t('ui.pct_used', { pct: pct.toFixed(0) })}
          </Text>
        ) : (
          <StatusBadge
            status={job.status}
            label={localizeJobStatus(job.status, t)}
          />
        )}
      </View>
      {band !== 'none' ? (
        <View style={s.pressureWrap} testID={`job-pressure-${job.job_id}`}>
          {/* Fidelity §7: 8px bar; fill colour = band tone (blue
              healthy / amber / red). Clamp the FILL only — the pct
              label above stays raw so an overspent job reads "143%". */}
          <View style={s.barTrack}>
            <View
              style={[
                s.barFill,
                {
                  width: `${Math.min(100, Math.max(0, pct ?? 0))}%`,
                  backgroundColor: toneFill[BAND_TONE[band]],
                },
              ]}
            />
          </View>
          <View style={s.rowBottom}>
            <Text style={s.leftText} numberOfLines={1}>
              {remaining != null && remaining < 0
                ? t('dashboard.over_by', {
                    amount: formatMoney(Math.abs(remaining).toFixed(2)),
                  })
                : t('dashboard.left', {
                    amount: formatMoney((remaining ?? 0).toFixed(2)),
                  })}
            </Text>
            {/* Status word: only states we can EVIDENCE from the
                summary (overspend / near-budget); nothing invented. */}
            {sum?.overspend ? (
              <Text style={[s.statusWord, { color: toneText.bad }]}>
                {t('jobs.status_over')}
              </Text>
            ) : band === 'amber' || band === 'red' ? (
              <Text style={[s.statusWord, { color: toneText[BAND_TONE[band]] }]}>
                {t('jobs.status_near')}
              </Text>
            ) : null}
          </View>
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
      {/* F4: archived jobs settle to their realised net profit (admin
          only by construction — contributors' summary is null). */}
      {archived && pressure
        ? (() => {
            const profit = netProfit(job);
            if (profit == null) return null;
            const contract = parseFloat(job.contract_value_ex_gst ?? '0');
            const actual = parseFloat(sum?.actual_ex_gst ?? '0');
            return (
              <View style={s.profitRow} testID={`job-profit-${job.job_id}`}>
                <Text style={s.profitLabel}>{t('jobs.net_profit')}</Text>
                <View style={s.profitRight}>
                  <Text
                    style={[s.profitValue, profit < 0 && s.profitValueNeg]}
                    numberOfLines={1}
                    adjustsFontSizeToFit
                    minimumFontScale={0.7}
                  >
                    {(profit >= 0 ? '+' : '−') +
                      formatMoney(Math.abs(profit).toFixed(2))}
                  </Text>
                  <Text style={s.profitCalc} numberOfLines={2}>
                    {t('jobs.profit_calc', {
                      rev: formatMoney(contract.toFixed(2)),
                      cost: formatMoney(actual.toFixed(2)),
                    })}
                  </Text>
                </View>
              </View>
            );
          })()
        : null}
    </TouchableOpacity>
  );
}

/** F4: archived-section header — count + the SUM of realised profits
 *  across archived jobs where it's computable (contract + summary both
 *  present). All-archived scope per operator decision (no archived_at
 *  field exists for an FY cut). If NO archived job has a computable
 *  profit, the sum line is omitted rather than showing $0.00. */
function ArchivedHeader({
  jobs,
  isAdmin,
}: {
  jobs: JobPublic[];
  isAdmin: boolean;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const archived = jobs.filter((j) => j.status !== 'active');
  const profits = isAdmin
    ? archived.map(netProfit).filter((p): p is number => p != null)
    : [];
  const total = profits.reduce((a, b) => a + b, 0);
  return (
    <View style={s.archivedHeaderRow} testID="jobs-archived-header">
      <Text style={s.archivedHeader}>
        {t('job.archived_section')}
        {archived.length > 0 ? ` (${archived.length})` : ''}
      </Text>
      {profits.length > 0 ? (
        <Text
          style={[s.archivedTotal, total < 0 && s.profitValueNeg]}
          numberOfLines={1}
          adjustsFontSizeToFit
          minimumFontScale={0.7}
        >
          {/* Partial coverage must say so: jobs with no contract can't
              settle a profit, and a sum over 3 of 5 jobs presented as
              "总净利" would read as complete (review finding). */}
          {t(
            profits.length === archived.length
              ? 'jobs.archived_total'
              : 'jobs.archived_total_partial',
            {
              sum:
                (total >= 0 ? '+' : '−') +
                formatMoney(Math.abs(total).toFixed(2)),
              n: profits.length,
              m: archived.length,
            },
          )}
        </Text>
      ) : null}
    </View>
  );
}

// B4-1: the job-detail modal and its sections moved to the pushed
// page at app/jobs/[id]/index.tsx. This file is the LIST only.

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
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
  // B4.5 (design ②): the list's single action = solid primary.
  newBtn: {
    backgroundColor: tokens.primary,
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderRadius: 9,
    minHeight: 36,
    justifyContent: 'center',
  },
  newBtnText: { color: '#ffffff', fontSize: 13, fontWeight: '600' },
  filterRow: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  filterPill: {
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  filterPillOn: { backgroundColor: tokens.ink, borderColor: tokens.ink },
  filterPillText: {
    fontSize: 12.5,
    fontWeight: '600',
    color: tokens.ink2,
    fontVariant: ['tabular-nums'],
  },
  filterPillTextOn: { color: '#ffffff', fontWeight: '700' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  loadingText: { marginTop: 12, color: '#64748b' },
  emptyText: { color: '#64748b', fontSize: 16 },
  errText: { color: '#b91c1c', fontSize: 16 },
  listContent: { paddingBottom: 24 },
  // O2-B: row is now a column (top line + optional pressure block);
  // rowTop carries the original horizontal name/code/badge layout.
  // Fidelity §7: each job is a CARD on the grey ground.
  row: {
    marginHorizontal: 16,
    marginBottom: 10,
    paddingVertical: 13,
    paddingHorizontal: 14,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
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
  pressureWrap: { marginTop: 2, gap: 6 },
  barTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: tokens.barTrack,
    overflow: 'hidden',
  },
  barFill: { height: 8, borderRadius: 4 },
  rowBottom: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 10,
  },
  leftText: {
    flexShrink: 1,
    fontSize: 12.5,
    color: tokens.ink2,
    fontVariant: ['tabular-nums'],
  },
  statusWord: { fontSize: 12, fontWeight: '700' },
  pctTop: {
    fontSize: 12.5,
    fontWeight: '800',
    fontVariant: ['tabular-nums'],
  },
  metricHint: { fontSize: 12, color: '#64748b', paddingVertical: 2 },
  rowName: { fontSize: 16, color: '#0f172a', fontWeight: '500' },
  rowCode: { fontSize: 13, color: '#64748b', marginTop: 2 },
  sep: { height: 0 },
  // Apple HIG: tappable target ≥ 44×44pt. Without this, the bare ×
  // glyph is too small to reach with a thumb, especially in the corner.
  // Tier 1B: Edit button in the job detail modal header.
  // Spending section: top-level summary only (total spent / budget /
  // remaining). Per-category breakdown removed per operator dogfood
  // signal — redundant with the per-expense list shown below.
  // M5: lifecycle section + archived-list styling.
  rowArchived: { opacity: 0.75 },
  profitRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 10,
    borderTopWidth: 1,
    borderTopColor: tokens.lineSoft,
    paddingTop: 8,
    marginTop: 2,
  },
  profitLabel: { fontSize: 12, color: tokens.ink2, paddingTop: 2 },
  profitRight: { flex: 1, alignItems: 'flex-end', minWidth: 0 },
  profitValue: {
    fontSize: 16.5,
    fontWeight: '800',
    color: tokens.ok,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.2,
  },
  profitValueNeg: { color: tokens.bad },
  profitCalc: {
    fontSize: 11,
    color: tokens.muted,
    marginTop: 1,
    fontVariant: ['tabular-nums'],
  },
  /* (calc line wraps to 2 lines at xlarge rather than clipping the
     ex-GST qualifier — see numberOfLines at the call site) */
  archivedHeaderRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 18,
    paddingBottom: 6,
  },
  archivedTotal: {
    flexShrink: 1,
    fontSize: 13,
    fontWeight: '800',
    color: tokens.ok,
    fontVariant: ['tabular-nums'],
  },
  // F4: padding moved to archivedHeaderRow (the Text is nested now).
  archivedHeader: {
    fontSize: 13,
    fontWeight: '600',
    color: tokens.muted,
    textTransform: 'uppercase',
  },
});
