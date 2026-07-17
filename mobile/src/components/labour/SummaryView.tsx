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
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import { useLabourSummary } from '../../api/hooks/useLabour';
import { useJobs } from '../../api/hooks/useJobs';
import { useMe } from '../../api/hooks/useAuth';
import { OptionPickerModal } from '../OptionPickerModal';
import {
  dateToISO,
  formatDateAU,
  formatMonthLabel,
  monthEnd,
  monthStart,
  shiftMonthISO,
  todayISO,
} from '../../util/dates';
import { formatDays, formatMoney } from '../../util/format';
import { useScaledStyles } from '../../ui/type';
import {
  IncompleteAmount,
  RateGapBanner,
  Segmented,
} from '../../ui/kit';
import { tokens } from '../../ui/tokens';
import { ClockIcon, DollarIcon, UsersIcon } from '../../ui/icons';

/**
 * L-C2: weekly labour summary (admin-only) — labour cost CAPTURE,
 * never payroll.
 *
 * B4-2: embedded as the Summary tab of the Labour screen (formerly
 * route ``/labour/summary``). GET /labour-summary is
 * require_admin on the backend; the view gates on /auth/me (fails
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

type SummaryViewProps = {
  onFixRates: () => void;
};

export function SummaryView({ onFixRates }: SummaryViewProps) {
  const s = useScaledStyles(base);
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

  // X-2 follow-up: explicit "user pulled" flag (house pattern) — see
  // jobs.tsx; isRefetching now also fires on app-resume refetches.
  const [userRefreshing, setUserRefreshing] = useState(false);
  const refreshControl = (
    <RefreshControl
      refreshing={userRefreshing}
      onRefresh={() => {
        setUserRefreshing(true);
        void Promise.allSettled([
          summary.refetch(),
          jobs.refetch(),
          me.refetch(),
        ]).finally(() => setUserRefreshing(false));
      }}
      tintColor="#1e293b"
    />
  );

  // B4-3: bar scale — the range's largest per-worker worker-days.
  const maxWorkerDays = useMemo(
    () =>
      Math.max(
        1,
        ...(summary.data?.workers ?? []).map((w) => parseFloat(w.total_days)),
      ),
    [summary.data],
  );

  const empty =
    summary.data &&
    summary.data.workers.length === 0 &&
    summary.data.jobs.length === 0;

  return (
    <View style={s.root}>
      {me.isLoading ? (
        <View style={s.state}>
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : me.isError ? (
        // Unresolved identity ≠ forbidden — offer an in-view retry
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
          {/* B3: kit Segmented replaces the hand-rolled mode chips. */}
          <Segmented
            options={[
              { value: 'month', label: t('labour.range_month') },
              { value: 'week', label: t('labour.range_week') },
            ]}
            value={mode}
            onChange={setMode}
            testID="summary-mode"
          />
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
          ) : summary.isError && !summary.data ? (
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
                {/* Preview-parity: three stat cards (cost / hours /
                    worker-days) instead of stacked rows. */}
                <View style={s.statCards}>
                  <View style={s.statCard} testID="summary-total-cost">
                    <View style={[s.statIcon, { backgroundColor: tokens.okBg }]}>
                      <DollarIcon size={15} color={tokens.ok} />
                    </View>
                    <Text style={s.statCardLabel}>{t('labour.total_cost')}</Text>
                    <Text
                      style={s.statCardValue}
                      numberOfLines={1}
                      adjustsFontSizeToFit
                      minimumFontScale={0.7}
                    >
                      <IncompleteAmount
                        formatted={formatMoney(summary.data.total_labour_cost)}
                        incomplete={
                          summary.data.entries_costed <
                          summary.data.entries_total
                        }
                      />
                    </Text>
                  </View>
                  <View style={s.statCard}>
                    <View style={[s.statIcon, { backgroundColor: tokens.warnBg }]}>
                      <ClockIcon size={15} color={tokens.warnMid} />
                    </View>
                    <Text style={s.statCardLabel}>{t('labour.total_hours')}</Text>
                    <Text
                      style={s.statCardValue}
                      numberOfLines={1}
                      adjustsFontSizeToFit
                      minimumFontScale={0.7}
                    >
                      {t('labour.hours_value', {
                        hours: formatDays(summary.data.total_hours),
                      })}
                    </Text>
                  </View>
                  <View style={s.statCard}>
                    <View style={[s.statIcon, { backgroundColor: tokens.sel }]}>
                      <UsersIcon size={15} color={tokens.primary} />
                    </View>
                    <Text style={s.statCardLabel}>
                      {t('labour.job_worker_days_label')}
                    </Text>
                    <Text
                      style={s.statCardValue}
                      numberOfLines={1}
                      adjustsFontSizeToFit
                      minimumFontScale={0.7}
                    >
                      {formatDays(summary.data.total_days)}
                    </Text>
                  </View>
                </View>
                {/* O2-B polish #6: define the metric inline. */}
                <Text style={s.modeHint} testID="summary-days-hint">
                  {t('labour.days_metrics_hint')}
                </Text>
                {/* B3: kit RateGapBanner replaces the O2-C (U9)
                    incomplete-cost note — same data (entries_costed /
                    entries_total), same destination (worker rates). */}
                <RateGapBanner
                  missing={
                    summary.data.entries_total - summary.data.entries_costed
                  }
                  total={summary.data.entries_total}
                  onPress={onFixRates}
                  testID="summary-incomplete"
                />
              </View>

              {empty ? (
                <Text style={s.emptyText} testID="summary-empty">
                  {t('labour.summary_empty')}
                </Text>
              ) : (
                <>
                  <Text style={s.sectionHeader}>{t('labour.by_worker')}</Text>
                  {/* B4-3 (preview parity): categorical bar per worker,
                      sized by worker-days relative to the range max.
                      cat-1..4 palette — deliberately NOT the action
                      blue and NOT the ok/warn/bad status colours. */}
                  {summary.data.workers.map((w, wi) => (
                    <View
                      key={w.worker_id}
                      style={s.totalsRow}
                      testID={`summary-worker-${w.worker_id}`}
                    >
                      <View style={s.totalsLine}>
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
                      <WorkerBar
                        value={parseFloat(w.total_days)}
                        max={maxWorkerDays}
                        index={wi}
                      />
                    </View>
                  ))}

                  <Text style={s.sectionHeader}>{t('labour.by_job')}</Text>
                  {summary.data.jobs.map((j) => (
                    <View
                      key={j.job_id}
                      style={s.totalsRow}
                      testID={`summary-job-${j.job_id}`}
                    >
                      <View style={s.totalsLine}>
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
    </View>
  );
}


/** B4-3: minimal categorical bar (no chart library) — width is the
 *  worker's share of the range max; colour cycles cat-1..4. */
// forey §12: by-worker bars are ONE blue proportional bar (蓝条按占比).
// The cat1-4 ramp is the cost-composition palette (材料/人工/分包/其他)
// and is NOT a rotatable categorical scale — cat4 #E4E7EC on the
// barTrack is ~1.09:1, i.e. every 4th worker's bar disappeared.

function WorkerBar({
  value,
  max,
  index,
}: {
  value: number;
  max: number;
  index: number;
}) {
  const s = useScaledStyles(base);
  const pct = Number.isFinite(value) && max > 0 ? (value / max) * 100 : 0;
  const width = Math.min(100, Math.max(2, pct));
  return (
    <View style={s.workerBarTrack}>
      <View
        style={[
          s.workerBarFill,
          {
            width: `${width}%`,
            backgroundColor: tokens.primary,
          },
        ]}
      />
    </View>
  );
}

const base = StyleSheet.create({
  root: { flex: 1, backgroundColor: tokens.bg },
  pressed: { opacity: 0.5 },
  scroll: { padding: 16, gap: 12 },
  // B3: mode toggle now uses the kit Segmented (old modeChip styles
  // removed); the incomplete-cost note is the kit RateGapBanner.
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
    backgroundColor: tokens.surface,
  },
  statCards: { flexDirection: 'row', gap: 8 },
  statCard: {
    flex: 1,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 10,
    padding: 10,
    backgroundColor: '#ffffff',
    gap: 4,
  },
  statIcon: {
    width: 26,
    height: 26,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statCardLabel: { fontSize: 11, color: tokens.ink2 },
  statCardValue: {
    fontSize: 14.5,
    fontWeight: '700',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
  },
  emptyText: { color: '#64748b', fontSize: 14, textAlign: 'center', paddingVertical: 16 },
  sectionHeader: {
    fontSize: 15,
    fontWeight: '600',
    marginTop: 8,
    color: '#0f172a',
  },
  // B4-3 review fix: column wrapper + inner NON-wrapping line row, so a
  // long (esp. Chinese) name shrinks/truncates beside right-aligned
  // metrics exactly as pre-B4 — the full-width WorkerBar sits below.
  // F0 review: these rows are gapped children of the scroll body, so
  // painting them白 produced detached square slabs on the grey ground.
  // Left transparent with a ground-visible divider; the spec's card
  // treatment for this list is F5 (工时 visual pass).
  totalsRow: {
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: tokens.line,
  },
  totalsLine: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  workerBarTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: '#EDF1F6',
    overflow: 'hidden',
    marginTop: 6,
    width: '100%',
  },
  workerBarFill: { height: '100%', borderRadius: 999 },
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
