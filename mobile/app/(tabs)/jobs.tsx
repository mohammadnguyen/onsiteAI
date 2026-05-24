import { useMemo, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  ActivityIndicator,
  StyleSheet,
  TouchableOpacity,
  Modal,
  ScrollView,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import axios from 'axios';
import {
  useJob,
  useJobs,
  useJobBudgetSummary,
  type JobPublic,
  type JobBudgetSummary,
} from '../../src/api/hooks/useJobs';
import { useJobExpenses } from '../../src/api/hooks/useExpenses';
import { NewJobModal } from '../../src/components/NewJobModal';
import { RecentCapturesList } from '../../src/components/RecentCapturesList';
import { useSelectedJobStore } from '../../src/store/selectedJob';
import { formatMoney } from '../../src/util/format';

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

  const jobs = useMemo(() => data ?? [], [data]);

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
          data={jobs}
          keyExtractor={(item) => item.job_id}
          renderItem={({ item }) => (
            <JobRow job={item} onPress={() => setSelectedId(item.job_id)} />
          )}
          ItemSeparatorComponent={() => <View style={s.sep} />}
          contentContainerStyle={s.listContent}
        />
      )}
      <JobDetailModal
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

function JobRow({ job, onPress }: { job: JobPublic; onPress: () => void }) {
  const { t } = useTranslation();
  return (
    <TouchableOpacity onPress={onPress} style={s.row} testID={`job-row-${job.job_id}`}>
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
  const { data, isLoading, isError } = useJob(jobId);
  // Per-job spend + budget. Parallel fetch to useJob — both fire when
  // jobId is set, so spending is usually ready by the time the user has
  // scanned identity rows. Endpoint is admin-only; contributors get 403
  // and the section hides silently (see SpendingSection below).
  const summary = useJobBudgetSummary(jobId);
  // Per-job expense list (correction-loop slice). Same modal, below
  // spending. Limit 20 — operator-approved scope; extend later only
  // if 20 turns out not to be enough during dogfooding. Reuses the
  // existing RecentCapturesList row + navigation chrome.
  const jobExpenses = useJobExpenses(jobId, 20);
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
            <DetailRow
              label={t('job.contract')}
              value={data.contract_value_ex_gst ?? '-'}
            />
            <DetailRow
              label={t('job.budget')}
              value={data.total_budget_ex_gst ?? '-'}
            />
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

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
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
    justifyContent: 'flex-end',
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
  detailWrap: { padding: 16 },
  detailTitle: { fontSize: 22, fontWeight: '600', marginBottom: 16, color: '#0f172a' },
  detailRow: { flexDirection: 'row', paddingVertical: 6 },
  detailLabel: { flex: 1, color: '#64748b' },
  detailValue: { flex: 2, color: '#0f172a' },
  sectionHeader: { fontSize: 15, fontWeight: '600', marginTop: 20, marginBottom: 8, color: '#0f172a' },
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
});
