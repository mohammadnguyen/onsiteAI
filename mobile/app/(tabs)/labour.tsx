import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
  ScrollView,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { useMe } from '../../src/api/hooks/useAuth';
import { useJobs } from '../../src/api/hooks/useJobs';
import {
  AttendanceDeleteError,
  useLabourEntries,
  useSaveAttendance,
  useWorkers,
  type LabourEntryPublic,
} from '../../src/api/hooks/useLabour';
import { DatePills } from '../../src/components/DatePills';
import { OptionPickerModal } from '../../src/components/OptionPickerModal';
import {
  WorkerChecklist,
  type ChecklistRowState,
} from '../../src/components/WorkerChecklist';
import { todayISO } from '../../src/util/dates';
import { formatDays } from '../../src/util/format';

/**
 * L-B1: Labour tab — daily attendance tick screen.
 *
 * Fast-morning flow: Today + last-used job preselected; tick workers
 * (default full day); Save. The checklist is DECLARATIVE — saving
 * computes a diff against the server entries for the selected
 * (job, date): removals become per-entry DELETEs (run first), then new
 * and changed ticks go up as ONE all-or-nothing batch POST. After any
 * save attempt the entries query is invalidated, so the screen always
 * settles on server truth (the two phases are not atomic with each
 * other — accepted v1 shortcut, see useSaveAttendance).
 *
 * Lock rules mirror the backend's OD-1 (server stays authoritative):
 * admins edit anything; contributors only their OWN entries dated
 * today or later. The mirror uses device-local "today" for UI hints
 * only — when the clocks disagree (e.g. reverse UTC skew) a row can
 * stay visually unlocked while the server keeps rejecting; the 403 is
 * surfaced readably and nothing corrupts. Rows recorded by someone
 * else show a name-free reason — contributors cannot resolve recorder
 * names (the users list is admin-only by design).
 *
 * Attendance/days language only. No payroll concepts anywhere.
 */

/** In-session memory of the last job picked (module-level on purpose —
 * survives the root Slot unmounting the tab screen on drill-ins). */
let lastUsedJobId: string | null = null;

type RowEdit = { ticked: boolean; fraction: number };
type Banner = { kind: 'success' | 'error'; text: string };

/** House helper (same shape as the capture screen's): surface the
 * server's string detail; flatten Pydantic array details. */
function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d: { msg?: string; loc?: (string | number)[] }) => {
          const loc = Array.isArray(d.loc) ? d.loc.join('.') : '';
          return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '');
        })
        .join('; ');
    }
    if (error.message) return error.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export default function LabourScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const me = useMe();
  const jobs = useJobs();
  // include_inactive so a since-deactivated worker's existing entry on
  // the selected date still renders with a name (display is filtered
  // below to active-or-has-entry).
  const workers = useWorkers(true);

  const today = todayISO();
  const [date, setDate] = useState<string>(today);
  const [jobId, setJobId] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, RowEdit>>({});
  const [banner, setBanner] = useState<Banner | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const entries = useLabourEntries(jobId, date);
  const save = useSaveAttendance();

  const isAdmin = me.data?.role === 'admin';
  const myId = me.data?.user_id;

  const activeJobs = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active'),
    [jobs.data],
  );
  const selectedJob = activeJobs.find((j) => j.job_id === jobId) ?? null;

  // Default / repair the job selection: prefer the in-session
  // last-used job; fall back to the first active job. Also covers a
  // selected job being archived mid-session (it drops out of
  // activeJobs and the selection resets — to null when NO active job
  // remains, which also clears edits and disables Save).
  useEffect(() => {
    if (jobs.data === undefined) return;
    if (activeJobs.length === 0) {
      if (jobId !== null) setJobId(null);
      return;
    }
    if (jobId && activeJobs.some((j) => j.job_id === jobId)) return;
    const candidate =
      lastUsedJobId && activeJobs.some((j) => j.job_id === lastUsedJobId)
        ? lastUsedJobId
        : activeJobs[0].job_id;
    setJobId(candidate);
  }, [activeJobs, jobId, jobs.data]);

  // A new (job, date) context invalidates local edits — the checklist
  // re-derives from that day's server entries.
  useEffect(() => {
    setEdits({});
    setBanner(null);
  }, [jobId, date]);

  const entriesByWorker = useMemo(() => {
    const m = new Map<string, LabourEntryPublic>();
    for (const e of entries.data ?? []) m.set(e.worker_id, e);
    return m;
  }, [entries.data]);

  const rows: ChecklistRowState[] = useMemo(() => {
    return (workers.data ?? [])
      .filter((w) => w.is_active || entriesByWorker.has(w.worker_id))
      .map((w) => {
        const entry = entriesByWorker.get(w.worker_id) ?? null;
        const canModify =
          isAdmin ||
          (entry !== null &&
            entry.recorded_by_user_id === myId &&
            entry.work_date >= today);
        const locked = entry !== null && !canModify;
        // A locked row always displays SERVER truth — a local edit
        // made before a competing refetch locked it must not keep
        // driving the display.
        const edit = locked ? undefined : edits[w.worker_id];
        const ticked = edit ? edit.ticked : entry !== null;
        const fraction = edit
          ? edit.fraction
          : entry
            ? Number(entry.day_fraction)
            : 1;
        const lockReason = !locked
          ? null
          : entry && entry.recorded_by_user_id !== myId
            ? t('labour.locked_other')
            : t('labour.locked_past');
        return { worker: w, ticked, fraction, locked, lockReason };
      });
  }, [workers.data, entriesByWorker, edits, isAdmin, myId, today, t]);

  // The diff save (approved L-B1 semantics): unticked existing rows
  // become DELETEs; ticked rows are sent in the batch only when NEW or
  // CHANGED — untouched rows stay out, so another user's entries are
  // never re-submitted (which would 403 for contributors).
  const diff = useMemo(() => {
    const deletes: { entryId: string; workerId: string }[] = [];
    const batch: { worker_id: string; day_fraction: number }[] = [];
    for (const row of rows) {
      if (row.locked) continue;
      const entry = entriesByWorker.get(row.worker.worker_id) ?? null;
      if (entry && !row.ticked) {
        deletes.push({ entryId: entry.entry_id, workerId: row.worker.worker_id });
      } else if (
        row.ticked &&
        (!entry || Number(entry.day_fraction) !== row.fraction)
      ) {
        batch.push({
          worker_id: row.worker.worker_id,
          day_fraction: row.fraction,
        });
      }
    }
    return { deletes, batch };
  }, [rows, entriesByWorker]);

  const tickedRows = rows.filter((r) => r.ticked);
  const totalDays = tickedRows.reduce((acc, r) => acc + r.fraction, 0);

  const isFuture = date > today;
  const entriesReady = !!jobId && !entries.isLoading && !entries.isError;
  const hasChanges = diff.deletes.length + diff.batch.length > 0;
  // Server caps a batch at 50 entries; block client-side with a
  // readable message instead of surfacing the raw Pydantic 422.
  // Deliberately NOT chunked — chunking would silently break the
  // backend's all-or-nothing batch semantics.
  const overBatchCap = diff.batch.length > 50;
  const saveDisabled =
    !hasChanges || save.isPending || isFuture || !entriesReady || overBatchCap;

  const mapLabourDetail = (detail: string): string => {
    if (detail.includes('cannot exceed 1.0')) {
      return `${t('labour.error_day_total')}\n${detail}`;
    }
    if (detail.includes('Job is archived')) return t('labour.error_job_archived');
    if (detail.includes('too far in the past')) {
      return t('labour.error_date_too_old');
    }
    if (detail.includes('is deactivated')) {
      return `${t('labour.error_worker_deactivated')}\n${detail}`;
    }
    if (detail.includes('recorded by someone else')) {
      return t('labour.error_recorded_by_other');
    }
    if (detail.includes('own entries for today')) {
      return t('labour.error_own_today_only');
    }
    return detail;
  };

  // Synchronous double-tap guard: save.isPending only flips after a
  // re-render, so two rapid taps could both enter onSave otherwise.
  const savingRef = useRef(false);

  const onSave = async () => {
    if (saveDisabled || !jobId || savingRef.current) return;
    savingRef.current = true;
    setBanner(null);
    const savedWorkers = tickedRows.length;
    const savedDays = formatDays(totalDays);
    try {
      await save.mutateAsync({
        jobId,
        date,
        deletes: diff.deletes,
        batch: diff.batch,
      });
      setEdits({});
      setBanner({
        kind: 'success',
        text: t('labour.save_success', {
          workers: savedWorkers,
          days: savedDays,
        }),
      });
    } catch (err) {
      let text: string;
      if (err instanceof AttendanceDeleteError) {
        const name =
          rows.find((r) => r.worker.worker_id === err.workerId)?.worker
            .display_name ?? '';
        const detail = extractErrorMessage(
          err.cause,
          t('capture.error_network'),
        );
        text = `${t('labour.error_delete_failed', { name })}\n${mapLabourDetail(detail)}`;
      } else {
        text = mapLabourDetail(
          extractErrorMessage(err, t('capture.error_network')),
        );
      }
      setBanner({ kind: 'error', text });
    } finally {
      savingRef.current = false;
    }
  };

  const onToggle = (workerId: string) => {
    setBanner(null);
    const row = rows.find((r) => r.worker.worker_id === workerId);
    if (!row || row.locked) return;
    setEdits((prev) => ({
      ...prev,
      [workerId]: { ticked: !row.ticked, fraction: row.fraction },
    }));
  };

  const onSetFraction = (workerId: string, fraction: number) => {
    setBanner(null);
    const row = rows.find((r) => r.worker.worker_id === workerId);
    if (!row || row.locked) return;
    setEdits((prev) => ({ ...prev, [workerId]: { ticked: true, fraction } }));
  };

  const refreshControl = (
    <RefreshControl
      refreshing={entries.isRefetching || workers.isRefetching}
      onRefresh={() => {
        void entries.refetch();
        void workers.refetch();
        void jobs.refetch();
        // useMe has retry:false — pull-to-refresh is the in-screen
        // recovery path when /auth/me failed (identity drives locks).
        void me.refetch();
      }}
      tintColor="#1e293b"
    />
  );

  const initialLoading = jobs.isLoading || workers.isLoading || me.isLoading;

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <ScrollView
        contentContainerStyle={s.scroll}
        keyboardShouldPersistTaps="handled"
        refreshControl={refreshControl}
      >
        <View style={s.titleRow}>
          <Text style={s.title}>{t('labour.title')}</Text>
          {/* L-B2: admin-only entries to roster management and the
              attendance summary. useMe drives VISIBILITY only — both
              destinations re-gate themselves and the backend write/
              summary routes are require_admin (fails closed). */}
          {isAdmin ? (
            <View style={s.headerBtns}>
              <TouchableOpacity
                onPress={() =>
                  router.push('/labour/workers' as unknown as Href)
                }
                style={s.headerBtn}
                accessibilityRole="button"
                testID="labour-workers-btn"
              >
                <Text style={s.headerBtnText}>{t('labour.workers_entry')}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={() =>
                  router.push('/labour/summary' as unknown as Href)
                }
                style={s.headerBtn}
                accessibilityRole="button"
                testID="labour-summary-btn"
              >
                <Text style={s.headerBtnText}>{t('labour.summary_entry')}</Text>
              </TouchableOpacity>
            </View>
          ) : null}
        </View>

        <DatePills value={date} onChange={setDate} disabled={save.isPending} />

        <View style={s.jobRow}>
          <Text style={s.jobLabel}>{t('labour.job_label')}</Text>
          <TouchableOpacity
            style={s.jobChip}
            onPress={() => setPickerOpen(true)}
            disabled={save.isPending || activeJobs.length === 0}
            accessibilityRole="button"
            testID="labour-job-chip"
          >
            <Text style={s.jobChipText} numberOfLines={1}>
              {selectedJob?.job_name ?? '—'}
            </Text>
          </TouchableOpacity>
        </View>

        {isFuture ? (
          <Text style={s.warnText}>{t('labour.error_future_date')}</Text>
        ) : null}

        {initialLoading ? (
          <View style={s.center}>
            <ActivityIndicator size="large" color="#1e293b" />
          </View>
        ) : jobs.isError || workers.isError || me.isError ? (
          <Text style={s.errorCenter}>
            {workers.isError ? t('labour.workers_error') : t('common.error')}
          </Text>
        ) : activeJobs.length === 0 ? (
          <Text style={s.emptyText}>{t('labour.no_active_jobs')}</Text>
        ) : rows.length === 0 && !entries.isLoading ? (
          <Text style={s.emptyText}>{t('labour.empty_workers')}</Text>
        ) : entries.isError && !entries.data ? (
          <Text style={s.errorCenter}>{t('labour.entries_error')}</Text>
        ) : entries.isLoading ? (
          <View style={s.center}>
            <ActivityIndicator size="small" color="#1e293b" />
          </View>
        ) : (
          <>
            {entries.isError ? (
              // A background refetch failed but cached data exists —
              // keep the checklist visible (house expenses-list
              // behaviour); Save stays disabled until a clean refetch.
              <Text style={s.warnText}>{t('labour.entries_error')}</Text>
            ) : null}
            <WorkerChecklist
              rows={rows}
              disabled={save.isPending}
              onToggle={onToggle}
              onSetFraction={onSetFraction}
            />
          </>
        )}

        {overBatchCap ? (
          <Text style={s.warnText}>{t('labour.error_too_many')}</Text>
        ) : null}

        {me.data && !isAdmin && date < today && rows.length > 0 ? (
          <Text style={s.warnText}>{t('labour.past_notice')}</Text>
        ) : null}

        {banner ? (
          <View
            style={[
              s.banner,
              banner.kind === 'success' ? s.bannerOk : s.bannerError,
            ]}
            testID={`labour-banner-${banner.kind}`}
          >
            <Text
              style={
                banner.kind === 'success' ? s.bannerOkText : s.bannerErrorText
              }
            >
              {banner.text}
            </Text>
          </View>
        ) : null}

        <View style={s.footerRow}>
          <Text style={s.summaryText}>
            {t('labour.summary_line', {
              workers: tickedRows.length,
              days: formatDays(totalDays),
            })}
          </Text>
        </View>

        <TouchableOpacity
          onPress={() => void onSave()}
          disabled={saveDisabled}
          style={[s.saveBtn, saveDisabled && s.saveBtnDisabled]}
          accessibilityRole="button"
          testID="labour-save"
        >
          {save.isPending ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={s.saveBtnText}>{t('labour.save_cta')}</Text>
          )}
        </TouchableOpacity>
      </ScrollView>

      <OptionPickerModal
        visible={pickerOpen}
        title={t('labour.job_picker_title')}
        options={activeJobs.map((j) => ({ value: j.job_id, label: j.job_name }))}
        selected={jobId}
        onSelect={(v) => {
          if (v) {
            setJobId(v);
            lastUsedJobId = v;
          }
        }}
        onClose={() => setPickerOpen(false)}
        cancelLabel={t('common.cancel')}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  scroll: { padding: 16, gap: 14 },
  title: { fontSize: 24, fontWeight: '600', color: '#0f172a' },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  headerBtns: { flexDirection: 'row', gap: 8 },
  headerBtn: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  headerBtnText: { color: '#1e293b', fontSize: 14, fontWeight: '600' },
  jobRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  jobLabel: { color: '#475569', fontSize: 14 },
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
  center: { paddingVertical: 24, alignItems: 'center' },
  errorCenter: { color: '#b91c1c', fontSize: 14, textAlign: 'center', paddingVertical: 16 },
  emptyText: { color: '#64748b', fontSize: 14, textAlign: 'center', paddingVertical: 16 },
  warnText: { color: '#92400e', fontSize: 13 },
  banner: { borderRadius: 6, borderWidth: 1, padding: 12 },
  bannerOk: { backgroundColor: '#ecfdf5', borderColor: '#a7f3d0' },
  bannerError: { backgroundColor: '#fef2f2', borderColor: '#fecaca' },
  bannerOkText: { color: '#065f46', fontSize: 14 },
  bannerErrorText: { color: '#991b1b', fontSize: 14 },
  footerRow: { flexDirection: 'row', justifyContent: 'flex-end' },
  summaryText: { color: '#475569', fontSize: 14, fontVariant: ['tabular-nums'] },
  saveBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
  },
  saveBtnDisabled: { opacity: 0.4 },
  saveBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
