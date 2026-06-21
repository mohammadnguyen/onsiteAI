import { useCallback, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  ActivityIndicator,
  StyleSheet,
  TouchableOpacity,
  Modal,
  ScrollView,
  Alert,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import axios from 'axios';
import {
  useJob,
  useJobs,
  useJobBudgetSummary,
  useUpdateJob,
  useDeleteJob,
  type JobPublic,
  type JobBudgetSummary,
} from '../../src/api/hooks/useJobs';
import { useFocusEffect, useRouter, type Href } from 'expo-router';
import { useJobExpenses } from '../../src/api/hooks/useExpenses';
import {
  useJobLabourRollup,
  type JobLabourRollup,
} from '../../src/api/hooks/useLabour';
import { useMe } from '../../src/api/hooks/useAuth';
import { NewJobModal } from '../../src/components/NewJobModal';
import { RecentCapturesList } from '../../src/components/RecentCapturesList';
import { useSelectedJobStore } from '../../src/store/selectedJob';
import {
  formatDays,
  formatMoney,
  contractEnteredFromExGst,
  contractGstFromEntered,
} from '../../src/util/format';

/**
 * L-D1: calendar month-to-date start ("YYYY-MM-01", device-local) for
 * the job-detail labour "This month" toggle. `to` is left open so the
 * range runs to today. Kept local to this screen (one small function)
 * rather than refactored into a shared util — matches the existing
 * per-screen preset pattern (dashboard, expenses list).
 */
function isoMonthStart(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  return `${now.getFullYear()}-${m}-01`;
}

/**
 * Mobile Polish slice (Half A): map the backend's job-status enum
 * ("active" / "completed") to a translated label. Unknown / future
 * statuses fall back to the raw value rather than rendering a raw
 * i18n key string ("job.status_xyz"), so a backend schema addition
 * never produces a broken UI surface — just a temporarily English
 * label until the i18n table is extended.
 */
function localizeJobStatus(status: string, t: TFunction): string {
  switch (status) {
    case 'active':
      return t('job.status_active');
    case 'completed':
      return t('job.status_completed');
    default:
      return status;
  }
}

export default function JobsScreen() {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useJobs();
  // Persist the selected job id across React mount/unmount cycles via
  // the global store. Local useState was lost when JobsScreen unmounted
  // during navigation to /expenses/{id}, which made multi-delete loops
  // dump the user on the Capture screen on return.
  const selectedId = useSelectedJobStore((s) => s.selectedJobId);
  const setSelectedId = useSelectedJobStore((s) => s.setSelectedJobId);
  // Mobile Job Management Lite — admin-only "+ New Job" modal flag.
  // The detail modal and the new-job modal are mutually exclusive by
  // user gesture (no UI path opens both); both are top-level <Modal>s
  // and React Native renders them at the OS level, so even concurrent
  // visible state would be visually layered, not crash-y.
  const [showNewJob, setShowNewJob] = useState(false);

  // Tier 1B follow-up: native <Modal> overlay re-present on tab refocus.
  // When the user pushes /jobs/[id]/edit (or any sub-route outside the
  // tabs group), iOS dismisses the <Modal> implicitly even though
  // `visible={!!selectedJobId}` remains true. On return, React sees no
  // state change and never triggers a fresh present, so the user lands
  // on the bare Jobs list. Forcing JobDetailModal to remount via a
  // bumped `key` re-triggers the native present. The first-focus guard
  // skips the very first mount (the modal renders correctly without
  // intervention there); zustand `getState()` reads the live store
  // value without making `selectedJobId` a callback dependency, which
  // would cause spurious remounts on row-tap.
  const [modalEpoch, setModalEpoch] = useState(0);
  const firstFocusRef = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (firstFocusRef.current) {
        firstFocusRef.current = false;
        return;
      }
      if (useSelectedJobStore.getState().selectedJobId != null) {
        setModalEpoch((e) => e + 1);
      }
    }, []),
  );

  const jobs = useMemo(() => data ?? [], [data]);

  // M5: active jobs first; archived (status=completed) jobs sit
  // below a labelled divider with muted styling — still visible and
  // tappable for history, out of the way for daily use.
  const listData = useMemo<JobListItem[]>(() => {
    const active = jobs.filter((j) => j.status === 'active');
    const archived = jobs.filter((j) => j.status !== 'active');
    const out: JobListItem[] = active.map((job) => ({ kind: 'job', job }));
    if (archived.length > 0) {
      out.push({ kind: 'archived-header' });
      archived.forEach((job) => out.push({ kind: 'job', job }));
    }
    return out;
  }, [jobs]);

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
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
      {isLoading ? (
        <View style={s.center}>
          <ActivityIndicator size="large" color="#1e293b" />
          <Text style={s.loadingText}>{t('jobs.loading')}</Text>
        </View>
      ) : isError ? (
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
                onPress={() => setSelectedId(item.job.job_id)}
              />
            ) : (
              <Text style={s.archivedHeader} testID="jobs-archived-header">
                {t('job.archived_section')}
              </Text>
            )
          }
          ItemSeparatorComponent={() => <View style={s.sep} />}
          contentContainerStyle={s.listContent}
        />
      )}
      <JobDetailModal
        key={modalEpoch}
        jobId={selectedId}
        onClose={() => setSelectedId(null)}
      />
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
  onPress,
}: {
  job: JobPublic;
  archived?: boolean;
  onPress: () => void;
}) {
  const { t } = useTranslation();
  return (
    <TouchableOpacity
      onPress={onPress}
      style={[s.row, archived && s.rowArchived]}
      testID={`job-row-${job.job_id}`}
    >
      <View style={s.rowMain}>
        <Text style={s.rowName}>{job.job_name}</Text>
        {job.job_code ? <Text style={s.rowCode}>{job.job_code}</Text> : null}
      </View>
      <Text style={[s.badge, job.status === 'active' ? s.badgeActive : s.badgeCompleted]}>
        {localizeJobStatus(job.status, t)}
      </Text>
    </TouchableOpacity>
  );
}

function JobDetailModal({
  jobId,
  onClose,
}: {
  jobId: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const { data, isLoading, isError } = useJob(jobId);
  // Per-job spend + budget. Parallel fetch to useJob — both fire when
  // jobId is set, so spending is usually ready by the time the user has
  // scanned identity rows. Endpoint is admin-only; contributors get 403
  // and the section hides silently (see SpendingSection below).
  const summary = useJobBudgetSummary(jobId);
  // L-D1: per-job labour rollup. Contributor-safe endpoint (200 for all
  // roles; hours + cost stripped server-side for non-admins), so
  // contributors now get a per-job rollup too — labourers / worker-days
  // / days-on-site. The range toggle switches all-time (null) vs
  // calendar month-to-date; no per-day drilldown by design.
  const [labourRange, setLabourRange] = useState<'all' | 'month'>('all');
  const labourRollup = useJobLabourRollup(
    jobId,
    labourRange === 'month' ? isoMonthStart() : null,
    null,
  );

  // Tier 1B: navigate to the job edit screen. selectedJobId stays in
  // the store, so when the user returns via router.back() the modal
  // re-opens at the same job and shows refetched data.
  const onEdit = () => {
    if (!jobId) return;
    const editHref = `/jobs/${jobId}/edit` as unknown as Href;
    router.push(editHref);
  };
  // Per-job expense list (correction-loop slice). Same modal, below
  // spending. Limit 20 — operator-approved scope; extend later only
  // if 20 turns out not to be enough during dogfooding. Reuses the
  // existing RecentCapturesList row + navigation chrome.
  const jobExpenses = useJobExpenses(jobId, 20);
  // M5: lifecycle actions. /auth/me drives VISIBILITY ONLY — the job
  // write routes are require_admin, so the backend stays
  // authoritative; contributors see no lifecycle affordances.
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  const updateJob = useUpdateJob(jobId ?? '');
  const deleteJob = useDeleteJob();
  const lifecycleBusy = updateJob.isPending || deleteJob.isPending;

  const performStatusChange = async (target: 'active' | 'completed') => {
    try {
      await updateJob.mutateAsync({ status: target });
      // ['jobs'] root invalidation refetches the modal's useJob — the
      // status row and the archive/reopen button swap in place.
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
              onClose();
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

  // M5: Delete is offered ONLY when the job's expense query has
  // loaded and returned zero RAW rows (the raw items include rejected
  // expenses, so zero here genuinely means empty; review-queue rows
  // can't exist without a parent expense). The server's v1A-3 guard
  // (409) remains the authority if this signal is ever stale.
  const emptyForDelete =
    isAdmin &&
    jobExpenses.isSuccess &&
    (jobExpenses.data?.items.length ?? 1) === 0;

  // Mobile Smoke Patch 1: <Modal> on iOS renders in its own native window,
  // so SafeAreaView inside it does NOT always pick up the device's top
  // inset (status bar / Dynamic Island). Read the inset explicitly via
  // useSafeAreaInsets and apply it as paddingTop on the header. The
  // SafeAreaView below excludes the top edge so we don't double-pad.
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={!!jobId} animationType="slide" onRequestClose={onClose} transparent={false}>
      <SafeAreaView style={s.safe} edges={['left', 'right', 'bottom']}>
        <View style={[s.modalHeader, { paddingTop: insets.top + 8 }]}>
          <TouchableOpacity
            onPress={onClose}
            style={s.closeBtnTouch}
            testID="job-detail-close"
            accessibilityRole="button"
            accessibilityLabel={t('common.close')}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Text style={s.closeBtn}>{'\u00d7'}</Text>
          </TouchableOpacity>
          {/* Tier 1B: Edit button. Only meaningful when a job has
              actually loaded (else there's nothing to edit). Admin-
              only on the backend; contributor will get 403 inline
              on save attempt. */}
          {data ? (
            <TouchableOpacity
              onPress={onEdit}
              style={s.editBtnTouch}
              testID="job-detail-edit"
              accessibilityRole="button"
              accessibilityLabel={t('job.edit')}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Text style={s.editBtnText}>{t('job.edit')}</Text>
            </TouchableOpacity>
          ) : null}
        </View>
        {isLoading ? (
          <View style={s.center}>
            <ActivityIndicator color="#1e293b" />
          </View>
        ) : isError || !data ? (
          <View style={s.center}>
            <Text style={s.errText}>{t('common.error')}</Text>
          </View>
        ) : (
          <ScrollView contentContainerStyle={s.detailWrap}>
            <Text style={s.detailTitle}>{data.job_name}</Text>
            <DetailRow label={t('job.code')} value={data.job_code ?? '-'} />
            <DetailRow label={t('job.status')} value={localizeJobStatus(data.status, t)} />
            {/* F1/Q1 + F2 money-visibility: contract / budget / GST /
                revenue are admin-only — contributors never see them.
                F2: contract shows the AS-ENTERED amount (gross for
                "Including GST") plus the derived ex-GST revenue + GST. */}
            {isAdmin ? (
              <>
                {(() => {
                  const storedEx =
                    data.contract_value_ex_gst != null
                      ? Number(data.contract_value_ex_gst)
                      : null;
                  const incl = data.gst_mode === 'inclusive';
                  const entered =
                    storedEx != null
                      ? contractEnteredFromExGst(storedEx, incl)
                      : null;
                  return (
                    <>
                      <DetailRow
                        label={t('job.contract_value')}
                        value={entered != null ? formatMoney(entered) : '-'}
                      />
                      <DetailRow
                        label={t('job.gst_mode_label')}
                        value={t(
                          incl ? 'job.gst_including' : 'job.gst_none_cash',
                        )}
                      />
                      {storedEx != null ? (
                        <DetailRow
                          label={t('job.ex_gst_revenue')}
                          value={formatMoney(storedEx)}
                        />
                      ) : null}
                      {entered != null ? (
                        <DetailRow
                          label={t('job.gst_amount')}
                          value={formatMoney(
                            contractGstFromEntered(entered, incl),
                          )}
                        />
                      ) : null}
                    </>
                  );
                })()}
                <DetailRow
                  label={t('job.budget')}
                  value={
                    data.total_budget_ex_gst != null
                      ? formatMoney(data.total_budget_ex_gst)
                      : t('job.no_budget_set')
                  }
                />
              </>
            ) : null}
            <DetailRow label={t('job.address')} value={data.site_address ?? '-'} />
            <Text style={s.sectionHeader}>{t('job.aliases')}</Text>
            {data.aliases.length === 0 ? (
              <Text style={s.muted}>-</Text>
            ) : (
              data.aliases.map((a) => (
                <Text key={a.alias_id} style={s.aliasRow}>
                  {a.alias_text}
                </Text>
              ))
            )}
            <SpendingSection summary={summary} />
            {isAdmin ? <MarginSection job={data} summary={summary} /> : null}
            <LabourDaysSection
              rollup={labourRollup}
              isAdmin={isAdmin}
              range={labourRange}
              onRangeChange={setLabourRange}
            />
            {/* Per-job expense list (correction-loop slice): show
                the recent expenses for this job below spending, so
                admins can drill into individual rows to correct
                miscategorisations / wrong amounts directly from
                the job context. Reuses RecentCapturesList — tap row
                navigates to expense detail -> Edit expense CTA. */}
            <RecentCapturesList
              query={jobExpenses}
              heading={t('job.expenses')}
              fromJobId={jobId ?? undefined}
            />
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
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.detailRow}>
      <Text style={s.detailLabel}>{label}</Text>
      <Text style={s.detailValue}>{value}</Text>
    </View>
  );
}

/**
 * Per-job spend + budget visibility inside the job detail modal.
 *
 * Scope is deliberately narrow (per the correction-centric framing
 * from dogfood feedback): show total spent / budget / remaining +
 * per-category breakdown. No recent expenses, no margin fields, no
 * thresholds beyond overspend-red. Helps the correction loop by
 * surfacing "what does this expense do to the budget" inline with
 * job identity, NOT a full job dashboard.
 *
 * Error semantics (per operator guardrail):
 *   - 403 (admin-only endpoint, contributor caller) -> hide silently
 *   - any other failure -> small non-blocking "couldn't load"
 *     message so dogfooding still captures the signal
 *   - loading state -> small inline indicator; does NOT block the
 *     rest of the modal (aliases / identity rows above are already
 *     rendered)
 */
function SpendingSection({
  summary,
}: {
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const { t } = useTranslation();
  const is403 =
    axios.isAxiosError(summary.error) &&
    summary.error.response?.status === 403;

  // Silent hide: contributor opened an admin-only endpoint. Expected
  // shape; no banner, no error chip, no section header.
  if (is403) return null;

  return (
    <>
      <Text style={s.sectionHeader}>{t('job.budgets_and_spending')}</Text>
      {summary.isLoading ? (
        <View style={s.spendingLoading} testID="job-spending-loading">
          <ActivityIndicator size="small" color="#64748b" />
          <Text style={s.spendingLoadingText}>
            {t('job.spending_loading')}
          </Text>
        </View>
      ) : summary.isError ? (
        <Text style={s.spendingError} testID="job-spending-error">
          {t('job.spending_load_error')}
        </Text>
      ) : summary.data ? (
        <SpendingBody data={summary.data} />
      ) : null}
    </>
  );
}

/**
 * L-D1: per-job labour rollup in the job detail modal.
 *
 * Contributor-safe: the /labour-rollup endpoint returns 200 for every
 * role with three money-free metrics — Labourers (distinct workers),
 * Worker-days (labour input), Days on site (distinct dates / duration) —
 * so "4 workers x 1 day" reads as 4 / 4 / 1, not "4 days". Total hours
 * and Labour cost are admin-only: the server already strips them to null
 * for contributors, and this UI ALSO gates them on `isAdmin` (defence in
 * depth — money never renders on a non-admin device). An All-time /
 * This-month toggle replaces date/week stepping. Empty data renders
 * zeros (no labour yet), never a hidden section.
 */
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
  const { t } = useTranslation();
  const row: JobLabourRollup | undefined = rollup.data?.[0];

  return (
    <View testID="job-labour-days">
      <Text style={s.sectionHeader}>{t('labour.job_rollup_header')}</Text>
      <View style={s.labourRangeRow}>
        {(['all', 'month'] as const).map((opt) => (
          <TouchableOpacity
            key={opt}
            testID={`job-labour-range-${opt}`}
            onPress={() => onRangeChange(opt)}
            style={[s.labourRangeChip, range === opt && s.labourRangeChipActive]}
          >
            <Text
              style={[
                s.labourRangeText,
                range === opt && s.labourRangeTextActive,
              ]}
            >
              {t(
                opt === 'all'
                  ? 'labour.range_all_time'
                  : 'labour.range_this_month',
              )}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {rollup.isLoading ? (
        <View style={s.spendingLoading} testID="job-labour-loading">
          <ActivityIndicator size="small" color="#64748b" />
        </View>
      ) : rollup.isError ? (
        <Text style={s.spendingError} testID="job-labour-error">
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
          {/* Admin-only money rows. The server already nulls these for
              contributors; gating on isAdmin too means cost/hours can
              never render on a non-admin device. */}
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

function SpendingBody({ data }: { data: JobBudgetSummary }) {
  const { t } = useTranslation();
  const budget = data.total_budget_ex_gst;
  const remaining = data.remaining_ex_gst;

  // Operator dogfood signal: the per-category breakdown adds visual
  // noise on mobile and is redundant with the per-expense list shown
  // below this section (支出明细 / Expenses). Stay top-level only:
  // total spent, budget, remaining. For per-category analysis the
  // admin web's budget summary remains the canonical surface.
  return (
    <View testID="job-spending-body">
      <DetailRow
        label={t('job.total_spent')}
        value={formatMoney(data.actual_ex_gst)}
      />
      <DetailRow
        label={t('job.budget')}
        value={budget != null ? formatMoney(budget) : t('job.no_budget_set')}
      />
      <View style={s.detailRow}>
        <Text style={s.detailLabel}>{t('job.remaining')}</Text>
        <Text
          style={[s.detailValue, data.overspend ? s.overspendValue : null]}
        >
          {remaining != null ? formatMoney(remaining) : '—'}
        </Text>
      </View>
    </View>
  );
}

/**
 * F1: admin-only expected-margin readout in the job detail modal.
 *
 * Shows Target margin % (the stored target_profit_ratio_pct), Current
 * margin (to date) = (contract - cost-so-far)/contract x100, and a
 * +/- vs-target indicator (green above / red below). "To date" is
 * deliberate: it is contract minus cost INCURRED so far, NOT realised
 * profit (future costs are excluded). Admin-only: the budget-summary
 * endpoint 403-hides for contributors so summary.data is present only
 * for admins, and the call site also gates on isAdmin (defence in
 * depth). Renders nothing when contract is missing/zero (can't compute).
 */
function MarginSection({
  job,
  summary,
}: {
  job: NonNullable<ReturnType<typeof useJob>['data']>;
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const { t } = useTranslation();
  const data = summary.data;
  if (!data) return null; // admin-only (contributor gets 403 -> no data)

  const contract =
    job.contract_value_ex_gst != null
      ? Number(job.contract_value_ex_gst)
      : null;
  const target =
    job.target_profit_ratio_pct != null
      ? Number(job.target_profit_ratio_pct)
      : null;

  // Current margin (to date) = (contract - cost-so-far)/contract, only
  // when a positive contract exists. No contract -> Current is hidden
  // (never a "--%" placeholder); zero cost yields 100.0%, a correct
  // "to date" value. Target still shows on its own if configured.
  const current =
    contract != null && contract > 0
      ? ((contract - Number(data.actual_ex_gst)) / contract) * 100
      : null;

  // Show the section if EITHER a target is configured OR a current
  // margin is computable; otherwise render nothing.
  if (target == null && current == null) return null;

  const delta = current != null && target != null ? current - target : null;

  return (
    <View testID="job-margin">
      <Text style={s.sectionHeader}>{t('job.margin_header')}</Text>
      {target != null ? (
        <DetailRow
          label={t('job.target_margin_pct')}
          value={`${target.toFixed(1)}%`}
        />
      ) : null}
      {current != null ? (
        <DetailRow
          label={t('job.current_margin_to_date')}
          value={`${current.toFixed(1)}%`}
        />
      ) : null}
      {delta != null ? (
        <View style={s.detailRow}>
          <Text style={s.detailLabel}>{t('job.margin_vs_target')}</Text>
          <Text
            style={[s.detailValue, delta >= 0 ? s.marginAbove : s.marginBelow]}
            testID="job-margin-delta"
          >
            {delta >= 0
              ? `▲ +${delta.toFixed(1)}%`
              : `▼ ${delta.toFixed(1)}%`}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  marginAbove: { color: '#16a34a', fontWeight: '600' },
  marginBelow: { color: '#dc2626', fontWeight: '600' },
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
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 16,
    backgroundColor: '#ffffff',
  },
  rowMain: { flex: 1 },
  rowName: { fontSize: 16, color: '#0f172a', fontWeight: '500' },
  rowCode: { fontSize: 13, color: '#64748b', marginTop: 2 },
  badge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    overflow: 'hidden',
    fontSize: 12,
    fontWeight: '600',
  },
  badgeActive: { backgroundColor: '#dcfce7', color: '#15803d' },
  badgeCompleted: { backgroundColor: '#e2e8f0', color: '#475569' },
  sep: { height: 1, backgroundColor: '#e2e8f0' },
  modalHeader: {
    flexDirection: 'row',
    // Close on the left, Edit on the right. justifyContent
    // space-between keeps them at the edges. iOS HIG-style: dismiss
    // on the leading edge, primary action on the trailing edge.
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 8,
    // paddingTop applied inline from useSafeAreaInsets — see JobDetailModal.
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  // Apple HIG: tappable target ≥ 44×44pt. Without this, the bare ×
  // glyph is too small to reach with a thumb, especially in the corner.
  closeBtnTouch: {
    minWidth: 44,
    minHeight: 44,
    paddingHorizontal: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeBtn: { fontSize: 30, lineHeight: 32, color: '#0f172a', fontWeight: '300' },
  // Tier 1B: Edit button in the job detail modal header.
  editBtnTouch: {
    minWidth: 44,
    minHeight: 44,
    paddingHorizontal: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  editBtnText: { fontSize: 16, color: '#1e293b', fontWeight: '600' },
  detailWrap: { padding: 16 },
  detailTitle: { fontSize: 22, fontWeight: '600', marginBottom: 16, color: '#0f172a' },
  detailRow: { flexDirection: 'row', paddingVertical: 6 },
  detailLabel: { flex: 1, color: '#64748b' },
  detailValue: { flex: 2, color: '#0f172a' },
  sectionHeader: { fontSize: 15, fontWeight: '600', marginTop: 20, marginBottom: 8, color: '#0f172a' },
  labourRangeRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  labourRangeChip: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 16,
    backgroundColor: '#f1f5f9',
  },
  labourRangeChipActive: { backgroundColor: '#0f172a' },
  labourRangeText: { fontSize: 13, color: '#475569' },
  labourRangeTextActive: { color: '#ffffff', fontWeight: '600' },
  muted: { color: '#94a3b8' },
  aliasRow: { paddingVertical: 4, color: '#0f172a' },
  budgetRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4 },
  budgetName: { color: '#0f172a' },
  budgetAmount: { color: '#0f172a', fontVariant: ['tabular-nums'] },
  // Spending section: top-level summary only (total spent / budget /
  // remaining). Per-category breakdown removed per operator dogfood
  // signal — redundant with the per-expense list shown below.
  spendingLoading: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    gap: 8,
  },
  spendingLoadingText: { color: '#64748b', fontSize: 13 },
  spendingError: {
    color: '#b91c1c',
    fontSize: 13,
    paddingVertical: 6,
  },
  overspendValue: { color: '#b91c1c', fontWeight: '600' },
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
  lifecycleSection: { marginTop: 24, gap: 10 },
  lifecycleBtn: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingVertical: 12,
    alignItems: 'center',
  },
  lifecycleBtnText: { color: '#1e293b', fontWeight: '600', fontSize: 15 },
  lifecycleBtnDisabled: { opacity: 0.5 },
  jobDeleteBtn: {
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    paddingVertical: 12,
    alignItems: 'center',
  },
  jobDeleteBtnText: { color: '#b91c1c', fontWeight: '600', fontSize: 15 },
});
