import { useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  TouchableOpacity,
  StyleSheet,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useJob,
  useJobBudgetSummary,
  useUpdateJob,
  useDeleteJob,
  type JobBudgetSummary,
} from '../../../src/api/hooks/useJobs';
import { useJobExpenses } from '../../../src/api/hooks/useExpenses';
import {
  useJobLabourRollup,
  type JobLabourRollup,
} from '../../../src/api/hooks/useLabour';
import { useMe } from '../../../src/api/hooks/useAuth';
import { RecentCapturesList } from '../../../src/components/RecentCapturesList';
import {
  formatDays,
  formatMoney,
  contractEnteredFromExGst,
  contractGstFromEntered,
} from '../../../src/util/format';
import { monthStart, monthEnd, todayISO } from '../../../src/util/dates';
import { localizeJobStatus } from '../../../src/util/jobStatus';
import { useScaledStyles } from '../../../src/ui/type';
import { useOneShotBack } from '../../../src/util/navigation';
import { BudgetBar, Chip, StatusBadge } from '../../../src/ui/kit';
import { tokens, type Tone } from '../../../src/ui/tokens';

/**
 * UI-kit v2 Batch 4-1: the job-details PAGE.
 *
 * Replaces the old native <Modal> presented from the Jobs tab — a
 * plain pushed route means back() works everywhere without the
 * modal focus-gate/epoch machinery, and expense drill-downs return
 * here naturally (the from=job return path is retired).
 *
 * Layout per the confirmed v2 preview: identity header, second-level
 * tab chips (Overview / Expenses / Labour / Files), the Financial
 * overview card (Revenue hero + contract/GST line + 4-grid + budget
 * bar) and the Projected-margin card on Overview. Files is a
 * placeholder pending its own project (receipts need backend +
 * storage decisions).
 *
 * Money rules unchanged: everything financial is admin-only (server
 * strips regardless; C-05/C-09 double-gate posture); the labour
 * rollup stays contributor-safe (money-free metrics for all roles).
 */

type DetailTab = 'overview' | 'expenses' | 'labour' | 'files' | 'notes';

export default function JobDetailScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const jobId = id ?? null;

  const onBack = useOneShotBack('/(tabs)/jobs');

  const { isLoading, data } = useJob(jobId);
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  // C-09: summary query admin-gated client-side (no guaranteed-403
  // request for contributors); server stays authoritative.
  const summary = useJobBudgetSummary(isAdmin ? jobId : null);
  const jobExpenses = useJobExpenses(jobId, 20);
  const [labourRange, setLabourRange] = useState<'all' | 'month'>('all');
  const labourRollup = useJobLabourRollup(
    jobId,
    labourRange === 'month' ? monthStart(todayISO()) : null,
    labourRange === 'month' ? monthEnd(todayISO()) : null,
  );

  const [tab, setTab] = useState<DetailTab>('overview');

  const updateJob = useUpdateJob(jobId ?? '');
  const deleteJob = useDeleteJob();
  const lifecycleBusy = updateJob.isPending || deleteJob.isPending;

  const performStatusChange = async (target: 'active' | 'completed') => {
    try {
      await updateJob.mutateAsync({ status: target });
    } catch (err) {
      const detail = axios.isAxiosError(err)
        ? err.response?.data?.detail
        : undefined;
      Alert.alert(
        t('common.error'),
        typeof detail === 'string' ? detail : t('job.lifecycle_error'),
      );
    }
  };

  const onArchive = () => {
    Alert.alert(
      t('job.archive_confirm_title'),
      t('job.archive_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.archive_cta'),
          onPress: () => void performStatusChange('completed'),
        },
      ],
    );
  };

  const onReopen = () => {
    Alert.alert(
      t('job.reopen_confirm_title'),
      t('job.reopen_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.reopen_cta'),
          onPress: () => void performStatusChange('active'),
        },
      ],
    );
  };

  const onDeleteJob = () => {
    Alert.alert(
      t('job.delete_confirm_title'),
      t('job.delete_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.delete_cta'),
          style: 'destructive',
          onPress: async () => {
            if (!jobId) return;
            try {
              await deleteJob.mutateAsync({ jobId });
              // Job is gone — leave the page.
              onBack();
            } catch (err) {
              const detail = axios.isAxiosError(err)
                ? err.response?.data?.detail
                : undefined;
              // A 409 carries the backend's "…Archive it instead."
              // guidance verbatim — exactly what the user should see.
              Alert.alert(
                t('common.error'),
                typeof detail === 'string' ? detail : t('job.lifecycle_error'),
              );
            }
          },
        },
      ],
    );
  };

  // M5: Delete only when the expense query loaded and returned zero
  // RAW rows (raw includes rejected — zero genuinely means empty).
  // The server's 409 guard remains the authority.
  const emptyForDelete =
    isAdmin &&
    jobExpenses.isSuccess &&
    (jobExpenses.data?.items.length ?? 1) === 0;

  const subline = data
    ? [
        data.job_code,
        data.site_address,
        ...data.aliases.map((a) => a.alias_text),
      ]
        .filter(Boolean)
        .join(' · ')
    : '';

  const TABS: Array<{ key: DetailTab; label: string }> = [
    { key: 'overview', label: t('job.tab_overview') },
    { key: 'expenses', label: t('job.tab_expenses') },
    { key: 'labour', label: t('job.tab_labour') },
    { key: 'files', label: t('job.tab_files') },
    { key: 'notes', label: t('job.tab_notes') },
  ];

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right', 'bottom']}>
      <View style={s.headerRow}>
        <TouchableOpacity
          onPress={onBack}
          hitSlop={12}
          testID="job-detail-back"
          accessibilityRole="button"
          style={s.backBtn}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('tabs.jobs')}</Text>
        </TouchableOpacity>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('job.details_title')}
        </Text>
        {/* C-05: Edit is a money surface — admin-only client gate on
            top of the server's require_admin. */}
        {data && isAdmin ? (
          <TouchableOpacity
            onPress={() =>
              router.push(`/jobs/${jobId}/edit` as unknown as Href)
            }
            hitSlop={12}
            testID="job-detail-edit"
            accessibilityRole="button"
            style={s.editBtn}
          >
            <Text style={s.editBtnText}>{t('job.edit')}</Text>
          </TouchableOpacity>
        ) : (
          <View style={s.editSpacer} />
        )}
      </View>

      {isLoading ? (
        <View style={s.center}>
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : !data ? (
        // Cached data keeps rendering on a failed refetch; the error
        // screen is only for a cold open with nothing to show.
        <View style={s.center}>
          <Text style={s.errText}>{t('common.error')}</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={s.wrap}>
          <View style={s.identityRow}>
            <Text style={s.jobName} numberOfLines={2}>
              {data.job_name}
            </Text>
            <StatusBadge
              status={data.status}
              label={localizeJobStatus(data.status, t)}
            />
          </View>
          {subline ? (
            <Text style={s.subline} numberOfLines={2}>
              {subline}
            </Text>
          ) : null}

          <View style={s.tabRow} testID="job-detail-tabs">
            {TABS.map((tb) => (
              <Chip
                key={tb.key}
                label={tb.label}
                selected={tab === tb.key}
                onPress={() => setTab(tb.key)}
                testID={`job-tab-${tb.key}`}
              />
            ))}
          </View>

          {tab === 'overview' ? (
            <>
              {isAdmin ? (
                <>
                  <FinancialOverviewCard job={data} summary={summary} />
                  <MarginCard job={data} summary={summary} />
                </>
              ) : (
                <DetailRow
                  label={t('job.status')}
                  value={localizeJobStatus(data.status, t)}
                />
              )}
              {isAdmin ? (
                <View style={s.lifecycleSection} testID="job-lifecycle">
                  {data.status === 'active' ? (
                    <TouchableOpacity
                      onPress={onArchive}
                      disabled={lifecycleBusy}
                      style={[
                        s.lifecycleBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-archive"
                      accessibilityRole="button"
                    >
                      <Text style={s.lifecycleBtnText}>
                        {t('job.archive_cta')}
                      </Text>
                    </TouchableOpacity>
                  ) : (
                    <TouchableOpacity
                      onPress={onReopen}
                      disabled={lifecycleBusy}
                      style={[
                        s.lifecycleBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-reopen"
                      accessibilityRole="button"
                    >
                      <Text style={s.lifecycleBtnText}>
                        {t('job.reopen_cta')}
                      </Text>
                    </TouchableOpacity>
                  )}
                  {emptyForDelete ? (
                    <TouchableOpacity
                      onPress={onDeleteJob}
                      disabled={lifecycleBusy}
                      style={[
                        s.jobDeleteBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-delete"
                      accessibilityRole="button"
                    >
                      <Text style={s.jobDeleteBtnText}>
                        {t('job.delete_cta')}
                      </Text>
                    </TouchableOpacity>
                  ) : null}
                </View>
              ) : null}
            </>
          ) : null}

          {tab === 'expenses' ? (
            <RecentCapturesList
              query={jobExpenses}
              heading={t('job.expenses')}
            />
          ) : null}

          {tab === 'labour' ? (
            <LabourDaysSection
              rollup={labourRollup}
              isAdmin={isAdmin}
              range={labourRange}
              onRangeChange={setLabourRange}
            />
          ) : null}

          {tab === 'files' ? (
            <View style={s.comingSoonBox} testID="job-files-placeholder">
              <Text style={s.comingSoonText}>
                {t('job.files_coming_soon')}
              </Text>
            </View>
          ) : null}

          {/* Notes: placeholder like Files — the backend has no notes
              field yet, so the real feature needs its own project. */}
          {tab === 'notes' ? (
            <View style={s.comingSoonBox} testID="job-notes-placeholder">
              <Text style={s.comingSoonText}>
                {t('job.notes_coming_soon')}
              </Text>
            </View>
          ) : null}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

/* ================= Financial overview (B4-1) ================= */

/** Budget-usage tone from the SERVER's per-job summary (same rule as
 *  the list's bandFor: overspend/red → bad, amber → warn, else ok). */
function summaryTone(sum: JobBudgetSummary): Tone | null {
  if (sum.percent_consumed == null) return null;
  const pct = parseFloat(sum.percent_consumed);
  if (sum.overspend || pct >= parseFloat(sum.effective_warning_red_pct)) {
    return 'bad';
  }
  if (pct >= parseFloat(sum.effective_warning_amber_pct)) return 'warn';
  return 'ok';
}

function FinancialOverviewCard({
  job,
  summary,
}: {
  job: NonNullable<ReturnType<typeof useJob>['data']>;
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();

  // F2 display-hint model (unchanged math): stored value is ALWAYS
  // ex-GST; "entered" reconstructs the as-typed gross for inclusive
  // contracts.
  const storedEx =
    job.contract_value_ex_gst != null
      ? Number(job.contract_value_ex_gst)
      : null;
  const incl = job.gst_mode === 'inclusive';
  const entered =
    storedEx != null ? contractEnteredFromExGst(storedEx, incl) : null;

  const is403 =
    axios.isAxiosError(summary.error) &&
    summary.error.response?.status === 403;

  const sum = summary.data;
  const pct =
    sum?.percent_consumed != null ? parseFloat(sum.percent_consumed) : null;
  const tone = sum ? summaryTone(sum) : null;

  return (
    <View style={s.card} testID="job-financial-overview">
      <Text style={s.cardTitle}>{t('job.budgets_and_spending')}</Text>

      {/* Revenue hero (job data — renders even while summary loads). */}
      {storedEx != null ? (
        <>
          <Text style={s.revLabel}>{t('job.ex_gst_revenue')}</Text>
          <Text style={s.revValue} testID="job-revenue">
            {formatMoney(storedEx)}
          </Text>
          <View style={s.contractLine}>
            <Text style={s.contractText}>
              {`${t('job.contract_value')} ${entered != null ? formatMoney(entered) : '—'}`}
            </Text>
            <View style={s.gstChip}>
              <Text style={s.gstChipText}>
                {t(incl ? 'job.gst_including' : 'job.gst_none_cash')}
              </Text>
            </View>
            {entered != null && incl ? (
              <Text style={s.contractText}>
                {`· ${t('job.gst_amount')} ${formatMoney(contractGstFromEntered(entered, incl))}`}
              </Text>
            ) : null}
          </View>
          <View style={s.cardDivider} />
        </>
      ) : null}

      {/* Spending 4-grid + bar (summary data, four-state). */}
      {is403 ? null : summary.isLoading ? (
        <View style={s.inlineLoading} testID="job-spending-loading">
          <ActivityIndicator size="small" color="#64748b" />
          <Text style={s.inlineLoadingText}>{t('job.spending_loading')}</Text>
        </View>
      ) : summary.isError && !sum ? (
        <Text style={s.inlineError} testID="job-spending-error">
          {t('job.spending_load_error')}
        </Text>
      ) : sum ? (
        <View testID="job-spending-body">
          <View style={s.grid}>
            <View style={s.gridCell}>
              <Text style={s.gridLabel}>{t('job.budget')}</Text>
              <Text style={s.gridValue}>
                {sum.total_budget_ex_gst != null
                  ? formatMoney(sum.total_budget_ex_gst)
                  : t('job.no_budget_set')}
              </Text>
            </View>
            <View style={s.gridCell}>
              <Text style={s.gridLabel}>{t('job.total_spent')}</Text>
              <Text style={s.gridValue}>{formatMoney(sum.actual_ex_gst)}</Text>
            </View>
            <View style={s.gridCell}>
              <Text style={s.gridLabel}>{t('job.total_paid_cash_out')}</Text>
              <Text style={s.gridValue}>{formatMoney(sum.actual_inc_gst)}</Text>
            </View>
            <View style={s.gridCell}>
              <Text style={s.gridLabel}>{t('job.remaining')}</Text>
              <Text
                style={[s.gridValue, sum.overspend ? s.overspendValue : null]}
              >
                {sum.remaining_ex_gst != null
                  ? formatMoney(sum.remaining_ex_gst)
                  : '—'}
              </Text>
            </View>
          </View>
          {pct != null && tone != null ? (
            <View style={s.barWrap}>
              <BudgetBar
                pctUsed={pct}
                tone={tone}
                leftText={t('ui.of_budget')}
              />
            </View>
          ) : null}
          <Text style={s.metricHint} testID="job-total-paid-hint">
            {t('job.total_paid_hint')}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

/* ================= Margin card (moved from the modal, B3 logic) ==== */

function MarginCard({
  job,
  summary,
}: {
  job: NonNullable<ReturnType<typeof useJob>['data']>;
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const data = summary.data;
  if (!data) return null; // admin-only (contributor gets no data)

  const contract =
    job.contract_value_ex_gst != null
      ? Number(job.contract_value_ex_gst)
      : null;
  const target =
    job.target_profit_ratio_pct != null
      ? Number(job.target_profit_ratio_pct)
      : null;

  // Current margin (to date) = (contract - cost-so-far)/contract, only
  // when a positive contract exists (display arithmetic on server
  // figures — unchanged since F1).
  const current =
    contract != null && contract > 0
      ? ((contract - Number(data.actual_ex_gst)) / contract) * 100
      : null;

  // Projected margin comes from the SERVER (budgeted_profit_ratio_pct);
  // zero/no budget -> no misleading 100% hero.
  const budget =
    data.total_budget_ex_gst != null
      ? Number(data.total_budget_ex_gst)
      : null;
  const projected =
    budget != null && budget > 0 && data.budgeted_profit_ratio_pct != null
      ? Number(data.budgeted_profit_ratio_pct)
      : null;

  if (target == null && current == null && projected == null) return null;

  const hero = projected ?? current;
  const heroIsProjected = projected != null;
  const delta = hero != null && target != null ? hero - target : null;

  return (
    <View style={s.card} testID="job-margin">
      <Text style={s.cardTitle}>{t('job.margin_header')}</Text>
      {hero != null ? (
        <>
          <Text style={s.marginHeroLabel}>
            {heroIsProjected
              ? t('job.projected_margin')
              : t('job.current_margin_to_date')}
          </Text>
          <View style={s.marginHeroRow}>
            <Text
              style={s.marginHeroValue}
              testID={
                heroIsProjected ? 'job-margin-projected' : 'job-margin-current'
              }
            >
              {hero.toFixed(1)}%
            </Text>
            {delta != null && target != null ? (
              <View
                style={[
                  s.marginPill,
                  delta >= 0 ? s.marginPillOk : s.marginPillBad,
                ]}
                testID="job-margin-delta"
              >
                <Text
                  style={[
                    s.marginPillText,
                    { color: delta >= 0 ? tokens.ok : tokens.bad },
                  ]}
                >
                  {t('job.margin_delta_pill', {
                    delta: `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}`,
                    target: target.toFixed(1),
                  })}
                </Text>
              </View>
            ) : null}
          </View>
          {heroIsProjected ? (
            <Text style={s.metricHint} testID="job-margin-projected-hint">
              {t('job.projected_margin_hint')}
            </Text>
          ) : null}
        </>
      ) : target != null ? (
        <DetailRow
          label={t('job.target_margin_pct')}
          value={`${target.toFixed(1)}%`}
        />
      ) : null}
      {heroIsProjected && current != null ? (
        <DetailRow
          label={t('job.current_margin_to_date')}
          value={`${current.toFixed(1)}%`}
        />
      ) : null}
      {current != null ? (
        <Text style={s.metricHint} testID="job-margin-todate-hint">
          {t('job.margin_todate_hint')}
        </Text>
      ) : null}
    </View>
  );
}

/* ================= Labour rollup (moved from the modal) ============ */

function LabourDaysSection({
  rollup,
  isAdmin,
  range,
  onRangeChange,
}: {
  rollup: ReturnType<typeof useJobLabourRollup>;
  isAdmin: boolean;
  range: 'all' | 'month';
  onRangeChange: (next: 'all' | 'month') => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const row: JobLabourRollup | undefined = rollup.data?.[0];

  return (
    <View testID="job-labour-days">
      <Text style={s.sectionHeader}>{t('labour.job_rollup_header')}</Text>
      <View style={s.labourRangeRow}>
        {(['all', 'month'] as const).map((opt) => (
          <Chip
            key={opt}
            label={t(
              opt === 'all' ? 'labour.range_all_time' : 'labour.range_this_month',
            )}
            selected={range === opt}
            onPress={() => onRangeChange(opt)}
            testID={`job-labour-range-${opt}`}
          />
        ))}
      </View>

      {rollup.isLoading ? (
        <View style={s.inlineLoading} testID="job-labour-loading">
          <ActivityIndicator size="small" color="#64748b" />
        </View>
      ) : rollup.isError ? (
        <Text style={s.inlineError} testID="job-labour-error">
          {t('labour.job_days_error')}
        </Text>
      ) : (
        <>
          {/* Three money-free metrics, shown to every role. */}
          <DetailRow
            label={t('labour.job_labourers_label')}
            value={String(row?.labourers ?? 0)}
          />
          <DetailRow
            label={t('labour.job_worker_days_label')}
            value={formatDays(row?.worker_days ?? 0)}
          />
          <DetailRow
            label={t('labour.job_days_on_site_label')}
            value={String(row?.days_on_site ?? 0)}
          />
          <Text style={s.metricHint} testID="labour-days-hint">
            {t('labour.days_metrics_hint')}
          </Text>
          {/* Admin-only money rows — server nulls these for
              contributors; the isAdmin gate is defence in depth. */}
          {isAdmin && row?.total_hours != null ? (
            <DetailRow
              label={t('labour.total_hours')}
              value={t('labour.hours_value', { hours: row.total_hours })}
            />
          ) : null}
          {isAdmin && row?.labour_cost != null ? (
            <DetailRow
              label={t('labour.job_cost_label')}
              value={formatMoney(row.labour_cost)}
            />
          ) : null}
        </>
      )}
    </View>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  const s = useScaledStyles(base);
  return (
    <View style={s.detailRow}>
      <Text style={s.detailLabel}>{label}</Text>
      <Text style={s.detailValue}>{value}</Text>
    </View>
  );
}

/* ================= styles ================= */

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  backBtn: {
    minWidth: 64,
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
  },
  backChevron: { fontSize: 30, lineHeight: 32, color: tokens.primary },
  backLabel: { fontSize: 15, color: tokens.primary, marginLeft: 2 },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 16,
    fontWeight: '700',
    color: tokens.ink,
  },
  editBtn: { minWidth: 64, minHeight: 44, alignItems: 'flex-end', justifyContent: 'center' },
  editBtnText: { fontSize: 15, fontWeight: '600', color: tokens.primary },
  editSpacer: { minWidth: 64 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  errText: { color: '#b91c1c', fontSize: 15 },
  wrap: { padding: 16, gap: 12, paddingBottom: 32 },

  identityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  jobName: { flex: 1, fontSize: 22, fontWeight: '700', color: tokens.ink },
  subline: { fontSize: 12.5, color: tokens.ink3 },

  tabRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 2 },

  card: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    padding: 14,
    backgroundColor: '#ffffff',
  },
  cardTitle: { fontSize: 13.5, fontWeight: '700', color: tokens.ink },
  cardDivider: {
    height: 1,
    backgroundColor: tokens.lineSoft,
    marginVertical: 10,
  },

  revLabel: { marginTop: 8, fontSize: 12, color: tokens.ink3 },
  revValue: {
    fontSize: 26,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.5,
  },
  contractLine: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 4,
  },
  contractText: {
    fontSize: 12,
    color: tokens.ink2,
    fontVariant: ['tabular-nums'],
  },
  gstChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.lineSoft,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  gstChipText: { fontSize: 10, fontWeight: '700', color: tokens.ink2 },

  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  gridCell: { width: '50%', paddingVertical: 6, paddingRight: 8 },
  gridLabel: { fontSize: 11.5, color: tokens.ink2 },
  gridValue: {
    fontSize: 15.5,
    fontWeight: '700',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    marginTop: 1,
  },
  overspendValue: { color: tokens.bad },
  barWrap: { marginTop: 6 },

  inlineLoading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
  },
  inlineLoadingText: { fontSize: 12.5, color: tokens.ink2 },
  inlineError: { fontSize: 12.5, color: '#b91c1c', paddingVertical: 6 },
  metricHint: { fontSize: 12, color: '#64748b', paddingVertical: 2, marginTop: 4 },

  marginHeroLabel: { fontSize: 12, color: tokens.ink3, marginTop: 8 },
  marginHeroRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 10,
    marginTop: 4,
    flexWrap: 'wrap',
  },
  marginHeroValue: {
    fontSize: 29,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.5,
  },
  marginPill: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  marginPillOk: { backgroundColor: tokens.okBg, borderColor: tokens.okBorder },
  marginPillBad: {
    backgroundColor: tokens.badBg,
    borderColor: tokens.badBorder,
  },
  marginPillText: {
    fontSize: 10.5,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },

  sectionHeader: {
    fontSize: 15,
    fontWeight: '700',
    color: tokens.ink,
    marginTop: 4,
  },
  labourRangeRow: { flexDirection: 'row', gap: 8, marginVertical: 8 },

  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    paddingVertical: 6,
    gap: 12,
  },
  detailLabel: { fontSize: 13.5, color: tokens.ink2, flexShrink: 0 },
  detailValue: {
    fontSize: 14.5,
    fontWeight: '600',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
    textAlign: 'right',
  },

  lifecycleSection: { gap: 10, marginTop: 4 },
  lifecycleBtn: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    backgroundColor: '#ffffff',
  },
  lifecycleBtnDisabled: { opacity: 0.5 },
  lifecycleBtnText: { fontSize: 14.5, fontWeight: '600', color: tokens.ink },
  jobDeleteBtn: {
    borderWidth: 1,
    borderColor: tokens.badBorder,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    backgroundColor: tokens.badBg,
  },
  jobDeleteBtnText: { fontSize: 14.5, fontWeight: '600', color: tokens.bad },

  comingSoonBox: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    padding: 24,
    alignItems: 'center',
    // On the grey ground a lineSoft fill is invisible — this box sits
    // in the detail scroll body, not inside a card.
    backgroundColor: tokens.surface,
  },
  comingSoonText: {
    fontSize: 13.5,
    color: tokens.ink2,
    textAlign: 'center',
    lineHeight: 20,
  },
});
