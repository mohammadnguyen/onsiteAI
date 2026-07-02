import { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Pressable,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import { useLabourSummary } from '../../src/api/hooks/useLabour';
import { useJobs } from '../../src/api/hooks/useJobs';
import { useMe } from '../../src/api/hooks/useAuth';
import { OptionPickerModal } from '../../src/components/OptionPickerModal';
import {
  dateToISO,
  formatDateAU,
  formatMonthLabel,
  monthEnd,
  monthStart,
  shiftMonthISO,
  todayISO,
} from '../../src/util/dates';
import { formatDays, formatMoney } from '../../src/util/format';

/**
 * L-C2: weekly labour summary (admin-only) — labour cost CAPTURE,
 * never payroll.
 *
 * Route: ``/labour/summary``, entered via the admin-only "Summary"
 * header button on the Labour tab. GET /labour-summary is
 * require_admin on the backend; the screen gates on /auth/me (fails
 * closed) and maps a server 403 to the forbidden state as backstop.
 *
 * Range model: a WEEK (Monday–Sunday) containing ``weekStart``;
 * chevrons step one week; "This week" resets. Per worker: days, hours,
 * labour cost. Per job: days on site (distinct dates = duration) AND
 * worker-days (labour input) + hours + cost. Totals are server-
 * computed; the client only FORMATS Decimal-strings (never sums) and
 * shows costs only where complete — a missing rate/hours leaves the
 * value out rather than guessing.
 *
 * Job filter: all jobs INCLUDING archived (history survives archiving).
 */

function shiftISO(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  return dateToISO(dt);
}

/** Monday of the week containing the given ISO date (local). */
function mondayOf(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const dow = dt.getDay(); // 0=Sun..6=Sat
  dt.setDate(dt.getDate() - ((dow + 6) % 7));
  return dateToISO(dt);
}

export default function LabourSummaryScreen() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const me = useMe();
  const jobs = useJobs();

  const today = todayISO();
  const thisMonday = mondayOf(today);
  const thisMonthStart = monthStart(today);
  // F5: Month is the default view; Week is preserved via the toggle.
  const [mode, setMode] = useState<'month' | 'week'>('month');
  const [weekStart, setWeekStart] = useState<string>(thisMonday);
  const [monthAnchor, setMonthAnchor] = useState<string>(thisMonthStart);
  const [jobId, setJobId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Derive [from, to] from the active mode — useLabourSummary already
  // accepts any range, so the data path is identical for week/month.
  const weekEnd = shiftISO(weekStart, 6);
  const rangeFrom = mode === 'month' ? monthAnchor : weekStart;
  const rangeTo = mode === 'month' ? monthEnd(monthAnchor) : weekEnd;
  const summary = useLabourSummary(rangeFrom, rangeTo, jobId);

  const isAdmin = me.data?.role === 'admin';
  const isForbidden =
    summary.isError &&
    axios.isAxiosError(summary.error) &&
    summary.error.response?.status === 403;

  const atCurrent =
    mode === 'month' ? monthAnchor >= thisMonthStart : weekStart >= thisMonday;

  const onPrevRange = () => {
    if (mode === 'month') setMonthAnchor((p) => shiftMonthISO(p, -1));
    else setWeekStart((p) => shiftISO(p, -7));
  };
  // Clamp forward nav so we never step into a future range (a stale
  // anchor across a midnight/period rollover could otherwise land on a
  // period with no possible data).
  const onNextRange = () => {
    if (mode === 'month') {
      setMonthAnchor((p) => {
        const next = shiftMonthISO(p, 1);
        const cur = monthStart(todayISO());
        return next > cur ? cur : next;
      });
    } else {
      setWeekStart((p) => {
        const next = shiftISO(p, 7);
        const cur = mondayOf(todayISO());
        return next > cur ? cur : next;
      });
    }
  };
  const onResetRange = () =>
    mode === 'month' ? setMonthAnchor(thisMonthStart) : setWeekStart(thisMonday);
  const rangeLabel =
    mode === 'month'
      ? formatMonthLabel(monthAnchor, i18n.language)
      : `${formatDateAU(weekStart)} – ${formatDateAU(weekEnd)}`;

  const selectedJob =
    jobId === null
      ? null
      : (jobs.data ?? []).find((j) => j.job_id === jobId) ?? null;

  const jobOptions = useMemo(
    () => [
      { value: null, label: t('labour.filter_all_jobs') },
      ...(jobs.data ?? []).map((j) => ({ value: j.job_id, label: j.job_name })),
    ],
    [jobs.data, t],
  );

  const onBack = () => {
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/labour');
  };

  const refreshControl = (
    <RefreshControl
      refreshing={summary.isRefetching}
      onRefresh={() => {
        void summary.refetch();
        void jobs.refetch();
        void me.refetch();
      }}
      tintColor="#1e293b"
    />
  );

  const empty =
    summary.data &&
    summary.data.workers.length === 0 &&
    summary.data.jobs.length === 0;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="summary-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.pressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('labour.summary_title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {me.isLoading ? (
        <View style={s.state}>
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : me.isError ? (
        // Unresolved identity ≠ forbidden — offer an in-screen retry
        // rather than a permission message. Still fails closed.
        <View style={s.state} testID="summary-me-error">
          <Text style={[s.stateText, s.errorText]}>{t('common.error')}</Text>
          <Pressable
            onPress={() => void me.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.pressed]}
            accessibilityRole="button"
            testID="summary-me-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : !isAdmin || isForbidden ? (
        <View style={s.state} testID="summary-forbidden">
          <Text style={s.stateText}>{t('labour.summary_forbidden')}</Text>
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={s.scroll}
          refreshControl={refreshControl}
        >
          <View style={s.modeRow}>
            {(['month', 'week'] as const).map((opt) => (
              <TouchableOpacity
                key={opt}
                onPress={() => setMode(opt)}
                style={[s.modeChip, mode === opt && s.modeChipActive]}
                accessibilityRole="button"
                testID={`summary-mode-${opt}`}
              >
                <Text
                  style={[s.modeChipText, mode === opt && s.modeChipTextActive]}
                >
                  {t(opt === 'month' ? 'labour.range_month' : 'labour.range_week')}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
          {/* O2-B polish #6: "Week" is a 7-day window, not a pay week —
              admins kept reading it as a payroll period. */}
          {mode === 'week' ? (
            <Text style={s.modeHint} testID="summary-week-hint">
              {t('labour.range_week_hint')}
            </Text>
          ) : null}

          <View style={s.rangeRow}>
            <TouchableOpacity
              onPress={onPrevRange}
              style={s.chevronBtn}
              accessibilityRole="button"
              accessibilityLabel={t('labour.range_earlier')}
              testID="summary-prev"
            >
              <Text style={s.chevronText}>{'‹'}</Text>
            </TouchableOpacity>
            <Text style={s.rangeLabel} testID="summary-range">
              {rangeLabel}
            </Text>
            <TouchableOpacity
              onPress={onNextRange}
              disabled={atCurrent}
              style={[s.chevronBtn, atCurrent && s.chevronDisabled]}
              accessibilityRole="button"
              accessibilityLabel={t('labour.range_later')}
              testID="summary-next"
            >
              <Text style={s.chevronText}>{'›'}</Text>
            </TouchableOpacity>
          </View>

          <View style={s.filterRow}>
            {!atCurrent ? (
              <TouchableOpacity
                onPress={onResetRange}
                style={s.resetPill}
                accessibilityRole="button"
                testID="summary-reset-range"
              >
                <Text style={s.resetPillText}>
                  {t(
                    mode === 'month'
                      ? 'labour.range_this_month'
                      : 'labour.range_this_week',
                  )}
                </Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              onPress={() => setPickerOpen(true)}
              style={s.jobChip}
              accessibilityRole="button"
              testID="summary-job-filter"
            >
              <Text style={s.jobChipText} numberOfLines={1}>
                {selectedJob?.job_name ?? t('labour.filter_all_jobs')}
              </Text>
            </TouchableOpacity>
          </View>

          {summary.isLoading ? (
            <View style={s.state}>
              <ActivityIndicator size="large" color="#1e293b" />
            </View>
          ) : summary.isError ? (
            <View style={s.state} testID="summary-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('labour.summary_error')}
              </Text>
              <Pressable
                onPress={() => void summary.refetch()}
                style={({ pressed }) => [s.linkBtn, pressed && s.pressed]}
                accessibilityRole="button"
                testID="summary-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : summary.data ? (
            <>
              <View style={s.totalCard} testID="summary-total">
                {/* F5: admin-first hierarchy — labour cost is the hero
                    number, then hours, then worker-days. */}
                <View style={s.totalRow}>
                  <Text style={s.totalLabel}>{t('labour.total_cost')}</Text>
                  <Text style={s.totalValue} testID="summary-total-cost">
                    {formatMoney(summary.data.total_labour_cost)}
                  </Text>
                </View>
                <View style={s.totalRow}>
                  <Text style={s.totalLabel}>{t('labour.total_hours')}</Text>
                  <Text style={s.totalValue}>
                    {t('labour.hours_value', {
                      hours: formatDays(summary.data.total_hours),
                    })}
                  </Text>
                </View>
                <View style={s.totalRow}>
                  <Text style={s.totalLabel}>
                    {t('labour.job_worker_days_label')}
                  </Text>
                  <Text style={s.totalValue}>
                    {formatDays(summary.data.total_days)}
                  </Text>
                </View>
                {/* O2-B polish #6: define the metric inline. */}
                <Text style={s.modeHint} testID="summary-days-hint">
                  {t('labour.days_metrics_hint')}
                </Text>
                {summary.data.entries_costed < summary.data.entries_total ? (
                  <Text style={s.incompleteNote} testID="summary-incomplete">
                    {t('labour.cost_incomplete', {
                      costed: summary.data.entries_costed,
                      total: summary.data.entries_total,
                    })}
                  </Text>
                ) : null}
              </View>

              {empty ? (
                <Text style={s.emptyText} testID="summary-empty">
                  {t('labour.summary_empty')}
                </Text>
              ) : (
                <>
                  <Text style={s.sectionHeader}>{t('labour.by_worker')}</Text>
                  {summary.data.workers.map((w) => (
                    <View
                      key={w.worker_id}
                      style={s.totalsRow}
                      testID={`summary-worker-${w.worker_id}`}
                    >
                      <Text style={s.totalsName} numberOfLines={1}>
                        {w.display_name}
                      </Text>
                      <View style={s.totalsMetrics}>
                        <Text style={s.totalsDays}>
                          {t('labour.days_value', {
                            days: formatDays(w.total_days),
                          })}
                        </Text>
                        {w.total_hours != null ? (
                          <Text style={s.totalsSub}>
                            {t('labour.hours_value', {
                              hours: formatDays(w.total_hours),
                            })}
                          </Text>
                        ) : null}
                        {w.labour_cost != null ? (
                          <Text style={s.totalsCost}>
                            {formatMoney(w.labour_cost)}
                          </Text>
                        ) : null}
                      </View>
                    </View>
                  ))}

                  <Text style={s.sectionHeader}>{t('labour.by_job')}</Text>
                  {summary.data.jobs.map((j) => (
                    <View
                      key={j.job_id}
                      style={s.totalsRow}
                      testID={`summary-job-${j.job_id}`}
                    >
                      <Text style={s.totalsName} numberOfLines={1}>
                        {j.job_name}
                      </Text>
                      <View style={s.totalsMetrics}>
                        <Text style={s.totalsDays}>
                          {t('labour.days_on_site_value', {
                            days: j.days_on_site,
                          })}
                        </Text>
                        <Text style={s.totalsSub}>
                          {t('labour.worker_days_value', {
                            days: formatDays(j.total_days),
                          })}
                        </Text>
                        {j.total_hours != null ? (
                          <Text style={s.totalsSub}>
                            {t('labour.hours_value', {
                              hours: formatDays(j.total_hours),
                            })}
                          </Text>
                        ) : null}
                        {j.labour_cost != null ? (
                          <Text style={s.totalsCost}>
                            {formatMoney(j.labour_cost)}
                          </Text>
                        ) : null}
                      </View>
                    </View>
                  ))}
                </>
              )}
            </>
          ) : null}
        </ScrollView>
      )}

      <OptionPickerModal
        visible={pickerOpen}
        title={t('labour.job_picker_title')}
        options={jobOptions}
        selected={jobId}
        onSelect={setJobId}
        onClose={() => setPickerOpen(false)}
        cancelLabel={t('common.cancel')}
      />
    </SafeAreaView>
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
  pressed: { opacity: 0.5 },
  backChevron: { fontSize: 28, color: '#1e293b', marginRight: 4, lineHeight: 28 },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { minWidth: 72 },
  scroll: { padding: 16, gap: 12 },
  modeRow: { flexDirection: 'row', gap: 8 },
  modeChip: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  modeChipActive: { backgroundColor: '#0f172a', borderColor: '#0f172a' },
  modeChipText: { color: '#475569', fontSize: 14, fontWeight: '600' },
  modeChipTextActive: { color: '#ffffff' },
  modeHint: { fontSize: 12, color: '#64748b', marginTop: 6 },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15, textAlign: 'center' },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  rangeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  chevronBtn: {
    minWidth: 44,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  chevronDisabled: { opacity: 0.4 },
  chevronText: { fontSize: 22, color: '#1e293b', lineHeight: 24 },
  rangeLabel: {
    fontSize: 15,
    fontWeight: '600',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  filterRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  resetPill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  resetPillText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  jobChip: {
    flexShrink: 1,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  jobChipText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  totalCard: {
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    padding: 16,
    gap: 8,
    backgroundColor: '#f8fafc',
  },
  totalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  totalLabel: { color: '#475569', fontSize: 15, fontWeight: '600' },
  incompleteNote: { color: '#92400e', fontSize: 12, marginTop: 2 },
  totalValue: {
    color: '#0f172a',
    fontSize: 20,
    fontWeight: '600',
    fontVariant: ['tabular-nums'],
  },
  emptyText: { color: '#64748b', fontSize: 14, textAlign: 'center', paddingVertical: 16 },
  sectionHeader: {
    fontSize: 15,
    fontWeight: '600',
    marginTop: 8,
    color: '#0f172a',
  },
  totalsRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  totalsName: {
    color: '#0f172a',
    fontSize: 15,
    flexShrink: 1,
    marginRight: 12,
    paddingTop: 1,
  },
  totalsMetrics: { alignItems: 'flex-end' },
  totalsDays: {
    color: '#0f172a',
    fontSize: 15,
    fontWeight: '500',
    fontVariant: ['tabular-nums'],
  },
  totalsSub: {
    color: '#64748b',
    fontSize: 13,
    fontVariant: ['tabular-nums'],
    marginTop: 1,
  },
  totalsCost: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '600',
    fontVariant: ['tabular-nums'],
    marginTop: 1,
  },
});
