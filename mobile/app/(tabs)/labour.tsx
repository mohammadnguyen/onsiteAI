import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  ScrollView,
  RefreshControl,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useMe } from '../../src/api/hooks/useAuth';
import { resolveApiErrorMessage } from '../../src/api/errors';
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
import { useLabourEditTargetStore } from '../../src/store/labourEditTarget';
import { formatDays } from '../../src/util/format';
import { computeTimeRange, formatHoursShort, hhmmFromServer } from '../../src/util/time';
import { Segmented } from '../../src/ui/kit';
import { RecordsView } from '../../src/components/labour/RecordsView';
import { WorkersView } from '../../src/components/labour/WorkersView';
import { SummaryView } from '../../src/components/labour/SummaryView';

/**
 * L-B1 / L-C3: Labour tab — daily attendance tick screen.
 *
 * Fast-morning flow: Today + last-used job preselected; tick workers
 * (default full day); optionally type a start->end time range; Save. The
 * checklist is DECLARATIVE — saving computes a diff against the server
 * entries for the selected (job, date): removals become per-entry
 * DELETEs (run first), then new and changed ticks go up as ONE
 * all-or-nothing batch POST. After any save attempt the entries query is
 * invalidated, so the screen always settles on server truth.
 *
 * L-C3 time range: hours are DERIVED server-side from the typed
 * start->end span (full span, no break, same-day). The client sends
 * canonical HH:MM and shows the live duration before save; the backend
 * recomputes and enforces ordering, so it stays the source of truth.
 * Clearing a previously-saved range prompts whether to also clear the
 * recorded hours, so the user can never think a cleared time silently
 * left old hours driving the labour cost.
 *
 * Lock rules mirror the backend's OD-1 (server stays authoritative):
 * admins edit anything; contributors only their OWN entries dated today
 * or later. The mirror uses device-local "today" for UI hints only.
 *
 * Attendance/hours/days language only. No payroll concepts anywhere.
 */

// In-session memory of the last job picked lives in the
// labourEditTarget store (was a module-level `let` here) so logout
// can reset it alongside the rest of the session state (audit B-02).

type RowEdit = {
  ticked: boolean;
  fraction: number;
  startText: string;
  endText: string;
};
type Banner = { kind: 'success' | 'error'; text: string };

export default function LabourScreen() {
  const { t } = useTranslation();
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
  // B4-2: second-level tabs. Attendance stays this screen's own body;
  // Records/Workers/Summary render as embedded views (extracted from
  // their retired pushed routes). Contributors get attendance+records
  // only — workers/summary remain admin surfaces (each view still
  // re-gates itself; backend authoritative).
  const [subTab, setSubTab] = useState<
    'attendance' | 'records' | 'workers' | 'summary'
  >('attendance');

  const entries = useLabourEntries(jobId, date);
  const save = useSaveAttendance();

  // L-E1: consume an "edit this day" handoff from the records screen.
  // One-shot — cleared immediately so a later tab focus can't re-apply
  // a stale target. B-04: remember the handed-off job id so the
  // selection-repair effect below can tell the user when that job is
  // archived instead of silently rebinding to a different job.
  const handoffJobRef = useRef<string | null>(null);
  // B-04: set by the selection-repair effect, consumed by the
  // (job,date) reset effect — see both effects below.
  const archivedHandoffPending = useRef(false);
  // B4-2: records is an embedded tab now, so the edit-day handoff is a
  // plain callback (the one-shot labourEditTarget mechanism is retired;
  // the store keeps only lastUsedJobId). B-04 archived-job detection is
  // unchanged: handoffJobRef feeds the selection-repair effect below.
  const onEditDay = (targetJobId: string, targetDate: string) => {
    setDate(targetDate);
    setJobId(targetJobId);
    handoffJobRef.current = targetJobId;
    useLabourEditTargetStore.getState().setLastUsedJobId(targetJobId);
    setSubTab('attendance');
  };

  const isAdmin = me.data?.role === 'admin';
  const myId = me.data?.user_id;

  const activeJobs = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active'),
    [jobs.data],
  );
  const selectedJob = activeJobs.find((j) => j.job_id === jobId) ?? null;

  // A new (job, date) context invalidates local edits — the checklist
  // re-derives from that day's server entries. B-04: this effect is
  // also the ONLY place the archived-handoff warning is applied.
  // DECLARATION ORDER MATTERS (re-verify finding): this reset effect
  // must run BEFORE the selection-repair effect below within a commit.
  // That way a flag the repair effect sets in pass N is first seen by
  // this effect in pass N+1 — the rebind commit, whose jobId is final
  // — so no later jobId change wipes the banner. Declared the other
  // way round, both effects ran in the handoff commit itself: the
  // flag was consumed prematurely and the rebind commit's re-run
  // wiped the banner after one frame (the original B-04 bug).
  useEffect(() => {
    setEdits({});
    if (archivedHandoffPending.current) {
      archivedHandoffPending.current = false;
      setBanner({ kind: 'error', text: t('labour.target_job_archived') });
    } else {
      setBanner(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, date]);

  // Default / repair the job selection: prefer the in-session
  // last-used job; fall back to the first active job. Also covers a
  // selected job being archived mid-session (it drops out of
  // activeJobs and the selection resets — to null when NO active job
  // remains, which also clears edits and disables Save).
  useEffect(() => {
    if (jobs.data === undefined) return;
    if (jobId && activeJobs.some((j) => j.job_id === jobId)) {
      // Selection valid — a pending handoff (if any) resolved cleanly.
      handoffJobRef.current = null;
      return;
    }
    // B-04: the records->edit handoff targeted a job that is NOT in
    // activeJobs (archived since, or archived history row). Flag it —
    // the (job,date) reset effect ABOVE applies the banner on the
    // rebind commit (see its declaration-order note).
    if (jobId && handoffJobRef.current === jobId) {
      handoffJobRef.current = null;
      archivedHandoffPending.current = true;
    }
    if (activeJobs.length === 0) {
      if (jobId !== null) setJobId(null);
      return;
    }
    const lastUsedJobId = useLabourEditTargetStore.getState().lastUsedJobId;
    const candidate =
      lastUsedJobId && activeJobs.some((j) => j.job_id === lastUsedJobId)
        ? lastUsedJobId
        : activeJobs[0].job_id;
    setJobId(candidate);
  }, [activeJobs, jobId, jobs.data]);

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
        // Prefill the typed inputs from the server's HH:MM:SS times
        // (trimmed to HH:MM); empty when the entry has no range.
        const startText = edit ? edit.startText : hhmmFromServer(entry?.start_time);
        const endText = edit ? edit.endText : hhmmFromServer(entry?.end_time);
        const time = computeTimeRange(startText, endText);
        // Surface pre-L-C3 hours-only entries (hours but no times) so the
        // user can see what drives the cost; hidden once a range is typed.
        const hasServerTimes = !!entry?.start_time && !!entry?.end_time;
        const legacyHours =
          entry &&
          entry.hours != null &&
          !hasServerTimes &&
          time.startValue === null &&
          time.endValue === null
            ? formatHoursShort(Number(entry.hours))
            : null;
        const lockReason = !locked
          ? null
          : entry && entry.recorded_by_user_id !== myId
            ? t('labour.locked_other')
            : t('labour.locked_past');
        return {
          worker: w,
          ticked,
          fraction,
          startText,
          endText,
          time,
          legacyHours,
          locked,
          lockReason,
        };
      });
  }, [workers.data, entriesByWorker, edits, isAdmin, myId, today, t]);

  // The diff save (L-B1 semantics, L-C3 time-aware): unticked existing
  // rows become DELETEs; ticked rows are sent only when NEW or CHANGED.
  //
  // L-C3 per ticked row:
  //  - both times typed & valid -> send start_time/end_time; backend
  //    derives hours and ignores any client hours.
  //  - one time only / end<=start / unparseable -> timesInvalid (Save
  //    blocked; the row shows the reason inline).
  //  - no times, server HAD a range -> the user cleared it: collected in
  //    clearedTimeWorkers so Save can ask whether to also clear hours.
  //  - no times, no server range -> a fraction-only change preserves any
  //    legacy hours (and absent times) by omitting both fields.
  const diff = useMemo(() => {
    const deletes: { entryId: string; workerId: string }[] = [];
    const batch: {
      worker_id: string;
      day_fraction: number;
      hours?: number | null;
      start_time?: string | null;
      end_time?: string | null;
    }[] = [];
    const clearedTimeWorkers: { workerId: string; fraction: number }[] = [];
    let timesInvalid = false;
    for (const row of rows) {
      if (row.locked) continue;
      const entry = entriesByWorker.get(row.worker.worker_id) ?? null;
      if (entry && !row.ticked) {
        deletes.push({ entryId: entry.entry_id, workerId: row.worker.worker_id });
        continue;
      }
      if (!row.ticked) continue;
      const ts = row.time;
      if (ts.parseError || ts.onePresent || ts.orderError) {
        timesInvalid = true;
        continue;
      }
      const serverStart = hhmmFromServer(entry?.start_time);
      const serverEnd = hhmmFromServer(entry?.end_time);
      const hadServerTimes = serverStart !== '' && serverEnd !== '';
      const fractionChanged =
        !entry || Number(entry.day_fraction) !== row.fraction;
      if (ts.ready) {
        const timesChanged =
          ts.startValue !== (serverStart || null) ||
          ts.endValue !== (serverEnd || null);
        if (fractionChanged || timesChanged) {
          batch.push({
            worker_id: row.worker.worker_id,
            day_fraction: row.fraction,
            start_time: ts.startValue,
            end_time: ts.endValue,
          });
        }
      } else if (hadServerTimes) {
        // Both inputs empty but the entry had a saved range — a clear.
        clearedTimeWorkers.push({
          workerId: row.worker.worker_id,
          fraction: row.fraction,
        });
      } else if (fractionChanged) {
        // No range involved: change the day fraction only and preserve
        // any existing hours + (absent) times by omitting those fields.
        batch.push({
          worker_id: row.worker.worker_id,
          day_fraction: row.fraction,
        });
      }
    }
    return { deletes, batch, clearedTimeWorkers, timesInvalid };
  }, [rows, entriesByWorker]);

  const tickedRows = rows.filter((r) => r.ticked);
  const totalDays = tickedRows.reduce((acc, r) => acc + r.fraction, 0);
  const totalHours = tickedRows.reduce((acc, r) => {
    if (r.time.ready && r.time.durationHours != null) {
      return acc + r.time.durationHours;
    }
    if (r.legacyHours) return acc + Number(r.legacyHours);
    return acc;
  }, 0);

  const isFuture = date > today;
  const entriesReady = !!jobId && !entries.isLoading && !entries.isError;
  const effectiveBatchCount = diff.batch.length + diff.clearedTimeWorkers.length;
  const hasChanges = diff.deletes.length + effectiveBatchCount > 0;
  // Server caps a batch at 50 entries; block client-side with a
  // readable message instead of surfacing the raw Pydantic 422.
  const overBatchCap = effectiveBatchCount > 50;
  const saveDisabled =
    !hasChanges ||
    save.isPending ||
    isFuture ||
    !entriesReady ||
    overBatchCap ||
    diff.timesInvalid;

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
    if (detail.includes('after start time')) return t('labour.error_time_order');
    if (detail.includes('both a start time')) return t('labour.error_time_one');
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
      // Clearing a saved range: ask once whether to also clear the
      // recorded hours so a removed time can't silently keep driving cost.
      let cleared: typeof diff.batch = [];
      if (diff.clearedTimeWorkers.length > 0) {
        const choice = await new Promise<'keep' | 'clear' | 'cancel'>(
          (resolve) => {
            Alert.alert(
              t('labour.clear_hours_title'),
              t('labour.clear_hours_message', {
                count: diff.clearedTimeWorkers.length,
              }),
              [
                {
                  text: t('common.cancel'),
                  style: 'cancel',
                  onPress: () => resolve('cancel'),
                },
                {
                  text: t('labour.clear_hours_keep'),
                  onPress: () => resolve('keep'),
                },
                {
                  text: t('labour.clear_hours_clear'),
                  style: 'destructive',
                  onPress: () => resolve('clear'),
                },
              ],
              { cancelable: true, onDismiss: () => resolve('cancel') },
            );
          },
        );
        if (choice === 'cancel') {
          savingRef.current = false;
          return;
        }
        cleared = diff.clearedTimeWorkers.map((c) => ({
          worker_id: c.workerId,
          day_fraction: c.fraction,
          start_time: null,
          end_time: null,
          // Keep: omit hours (server preserves). Clear: explicit null.
          ...(choice === 'clear' ? { hours: null } : {}),
        }));
      }
      await save.mutateAsync({
        jobId,
        date,
        deletes: diff.deletes,
        batch: [...diff.batch, ...cleared],
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
        const detail = resolveApiErrorMessage(
          err.cause,
          t,
          t('capture.error_network'),
        );
        text = `${t('labour.error_delete_failed', { name })}\n${mapLabourDetail(detail)}`;
      } else {
        text = mapLabourDetail(
          resolveApiErrorMessage(err, t, t('capture.error_network')),
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
      [workerId]: {
        ticked: !row.ticked,
        fraction: row.fraction,
        startText: row.startText,
        endText: row.endText,
      },
    }));
  };

  const onSetFraction = (workerId: string, fraction: number) => {
    setBanner(null);
    const row = rows.find((r) => r.worker.worker_id === workerId);
    if (!row || row.locked) return;
    setEdits((prev) => ({
      ...prev,
      [workerId]: {
        ticked: true,
        fraction,
        startText: row.startText,
        endText: row.endText,
      },
    }));
  };

  const onSetStart = (workerId: string, text: string) => {
    setBanner(null);
    const row = rows.find((r) => r.worker.worker_id === workerId);
    if (!row || row.locked) return;
    setEdits((prev) => ({
      ...prev,
      [workerId]: {
        ticked: true,
        fraction: row.fraction,
        startText: text,
        endText: row.endText,
      },
    }));
  };

  const onSetEnd = (workerId: string, text: string) => {
    setBanner(null);
    const row = rows.find((r) => r.worker.worker_id === workerId);
    if (!row || row.locked) return;
    setEdits((prev) => ({
      ...prev,
      [workerId]: {
        ticked: true,
        fraction: row.fraction,
        startText: row.startText,
        endText: text,
      },
    }));
  };

  // X-2 follow-up: explicit "user pulled" flag (house pattern) — with
  // refetchOnWindowFocus on, isRefetching also goes true on every app
  // resume, which would pin a phantom pull-spinner for the refetch
  // duration on weak networks.
  const [userRefreshing, setUserRefreshing] = useState(false);
  const refreshControl = (
    <RefreshControl
      refreshing={userRefreshing}
      onRefresh={() => {
        setUserRefreshing(true);
        void Promise.allSettled([
          entries.refetch(),
          workers.refetch(),
          jobs.refetch(),
          // useMe has retry:false — pull-to-refresh is the in-screen
          // recovery path when /auth/me failed (identity drives locks).
          me.refetch(),
        ]).finally(() => setUserRefreshing(false));
      }}
      tintColor="#1e293b"
    />
  );

  const initialLoading = jobs.isLoading || workers.isLoading || me.isLoading;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
      <View style={s.tabHeader}>
        <View style={s.titleRow}>
          <Text style={s.title}>{t('labour.title')}</Text>
        </View>
        {/* B4-2: second-level tabs (preview parity). Admin sees all
            four; contributors attendance+records only. Rendered OUTSIDE
            the per-tab scroll containers: Records/Workers carry their
            own virtualized lists, which must not nest in a ScrollView. */}
        <Segmented
          options={
            isAdmin
              ? [
                  { value: 'attendance', label: t('labour.tab_attendance') },
                  { value: 'records', label: t('labour.records_entry') },
                  { value: 'workers', label: t('labour.workers_entry') },
                  { value: 'summary', label: t('labour.summary_entry') },
                ]
              : [
                  { value: 'attendance', label: t('labour.tab_attendance') },
                  { value: 'records', label: t('labour.records_entry') },
                ]
          }
          value={subTab}
          onChange={setSubTab}
          testID="labour-subtabs"
        />
      </View>

      {subTab === 'records' ? <RecordsView onEditDay={onEditDay} /> : null}
      {subTab === 'workers' ? <WorkersView /> : null}
      {subTab === 'summary' ? <SummaryView onFixRates={() => setSubTab('workers')} /> : null}

      {subTab !== 'attendance' ? null : (
      <>
      {/* C-01: per-row time inputs sit low on the screen; without KAV
          the keyboard covers the row being typed into (login.tsx
          shape — padding on iOS, system resize on Android). */}
      <KeyboardAvoidingView
        style={s.kavFlex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
      <ScrollView
        contentContainerStyle={s.scroll}
        keyboardShouldPersistTaps="handled"
        refreshControl={refreshControl}
      >
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

        {/* L-E1: the saved-day banner — the missing signal that made
            attendance FEEL write-once. The ticks below are already
            seeded from this day's saved entries; changing + saving
            updates them (untick = delete). */}
        {(entries.data?.length ?? 0) > 0 ? (
          <Text style={s.savedDayBanner} testID="labour-saved-day-banner">
            {t('labour.saved_day_banner', { count: entries.data!.length })}
          </Text>
        ) : null}

        {initialLoading ? (
          <View style={s.center}>
            <ActivityIndicator size="large" color="#1e293b" />
          </View>
        ) : (jobs.isError && !jobs.data) ||
          (workers.isError && !workers.data) ||
          (me.isError && !me.data) ? (
          // Full-screen error ONLY when there is nothing cached to
          // show (X-2 follow-up: focus refetches can fail routinely
          // on weak networks; a failed refetch must never blank a
          // checklist the user was just working on).
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
              onSetStart={onSetStart}
              onSetEnd={onSetEnd}
            />
          </>
        )}

        {overBatchCap ? (
          <Text style={s.warnText}>{t('labour.error_too_many')}</Text>
        ) : null}

        {diff.timesInvalid ? (
          <Text style={s.warnText} testID="labour-times-invalid">
            {t('labour.error_time_fix')}
          </Text>
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
            {totalHours > 0
              ? t('labour.summary_line_hours', {
                  workers: tickedRows.length,
                  days: formatDays(totalDays),
                  hours: formatHoursShort(totalHours),
                })
              : t('labour.summary_line', {
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
      </KeyboardAvoidingView>
      </>
      )}

      <OptionPickerModal
        visible={pickerOpen}
        title={t('labour.job_picker_title')}
        options={activeJobs.map((j) => ({ value: j.job_id, label: j.job_name }))}
        selected={jobId}
        onSelect={(v) => {
          if (v) {
            setJobId(v);
            useLabourEditTargetStore.getState().setLastUsedJobId(v);
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
  kavFlex: { flex: 1 },
  scroll: { padding: 16, gap: 14 },
  title: { fontSize: 24, fontWeight: '600', color: '#0f172a' },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  // B4-2: fixed header region (title + subtabs) above the per-tab
  // scroll containers.
  tabHeader: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 8, gap: 6 },
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
  // L-E1: editing-a-saved-day indicator.
  savedDayBanner: {
    color: '#075985',
    fontSize: 13,
    backgroundColor: '#e0f2fe',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
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
