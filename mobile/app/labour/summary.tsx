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
import { dateToISO, formatDateAU, todayISO } from '../../src/util/dates';
import { formatDays } from '../../src/util/format';

/**
 * L-B2: attendance summary (admin-only) — labour DAYS, never pay.
 *
 * Route: ``/labour/summary``, entered via the admin-only "Summary"
 * header button on the Labour tab. GET /labour-summary is
 * require_admin on the backend; the screen gates on /auth/me (fails
 * closed) and maps a server 403 to the forbidden state as backstop.
 *
 * Range model: a rolling window of WINDOW_DAYS (14) ending at
 * ``end`` (default device-local today). Chevrons step the window by
 * 14 days; "Last 14 days" resets. Deliberately NOT anchored to any
 * pay cycle — this is an attendance/days view. Totals are
 * server-computed; the client formats Decimal-strings only.
 *
 * Job filter: all jobs INCLUDING archived (history survives
 * archiving by design).
 */

const WINDOW_DAYS = 14;

function shiftISO(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  return dateToISO(dt);
}

export default function LabourSummaryScreen() {
  const router = useRouter();
  const { t } = useTranslation();
  const me = useMe();
  const jobs = useJobs();

  const today = todayISO();
  const [end, setEnd] = useState<string>(today);
  const [jobId, setJobId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const from = shiftISO(end, -(WINDOW_DAYS - 1));
  const summary = useLabourSummary(from, end, jobId);

  const isAdmin = me.data?.role === 'admin';
  const isForbidden =
    summary.isError &&
    axios.isAxiosError(summary.error) &&
    summary.error.response?.status === 403;

  const atToday = end >= today;
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
          <View style={s.rangeRow}>
            <TouchableOpacity
              onPress={() => setEnd(shiftISO(end, -WINDOW_DAYS))}
              style={s.chevronBtn}
              accessibilityRole="button"
              accessibilityLabel={t('labour.range_earlier')}
              testID="summary-prev"
            >
              <Text style={s.chevronText}>{'‹'}</Text>
            </TouchableOpacity>
            <Text style={s.rangeLabel} testID="summary-range">
              {formatDateAU(from)} – {formatDateAU(end)}
            </Text>
            <TouchableOpacity
              onPress={() =>
                // Clamp to today — across a midnight rollover a stale
                // `end` could otherwise step the window into the
                // future (label showing dates with no possible data).
                setEnd((prev) => {
                  const next = shiftISO(prev, WINDOW_DAYS);
                  const t0 = todayISO();
                  return next > t0 ? t0 : next;
                })
              }
              disabled={atToday}
              style={[s.chevronBtn, atToday && s.chevronDisabled]}
              accessibilityRole="button"
              accessibilityLabel={t('labour.range_later')}
              testID="summary-next"
            >
              <Text style={s.chevronText}>{'›'}</Text>
            </TouchableOpacity>
          </View>

          <View style={s.filterRow}>
            {!atToday ? (
              <TouchableOpacity
                onPress={() => setEnd(today)}
                style={s.resetPill}
                accessibilityRole="button"
                testID="summary-reset-range"
              >
                <Text style={s.resetPillText}>{t('labour.range_last14')}</Text>
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
                <Text style={s.totalLabel}>{t('labour.total_days')}</Text>
                <Text style={s.totalValue}>
                  {t('labour.days_value', {
                    days: formatDays(summary.data.total_days),
                  })}
                </Text>
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
                      <Text style={s.totalsDays}>
                        {t('labour.days_value', {
                          days: formatDays(w.total_days),
                        })}
                      </Text>
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
                      <Text style={s.totalsDays}>
                        {t('labour.days_value', {
                          days: formatDays(j.total_days),
                        })}
                      </Text>
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
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#f8fafc',
  },
  totalLabel: { color: '#475569', fontSize: 15, fontWeight: '600' },
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
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  totalsName: { color: '#0f172a', fontSize: 15, flexShrink: 1, marginRight: 12 },
  totalsDays: { color: '#0f172a', fontSize: 15, fontVariant: ['tabular-nums'] },
});
