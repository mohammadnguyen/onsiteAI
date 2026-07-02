import { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  SectionList,
  ActivityIndicator,
  Pressable,
  TouchableOpacity,
  RefreshControl,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';

import {
  useLabourEntriesRange,
  useDeleteLabourEntry,
  useWorkers,
  type LabourEntryPublic,
} from '../../src/api/hooks/useLabour';
import { useJobs } from '../../src/api/hooks/useJobs';
import { useMe } from '../../src/api/hooks/useAuth';
import { useLabourEditTargetStore } from '../../src/store/labourEditTarget';
import { OptionPickerModal } from '../../src/components/OptionPickerModal';
import {
  formatDateAU,
  formatMonthLabel,
  monthEnd,
  monthStart,
  shiftMonthISO,
  todayISO,
} from '../../src/util/dates';
import { formatHoursShort, hhmmFromServer } from '../../src/util/time';
import { useScaledStyles } from '../../src/ui/type';
import i18n from '../../src/i18n';

/**
 * L-E1: attendance RECORDS browser — "see the records, then edit them".
 *
 * Dogfood finding: saved attendance FELT immutable because nothing ever
 * showed the saved rows — the tick screen quietly pre-seeds the selected
 * day, but there was no way to discover which days had records at all.
 * This screen lists a month of entries (newest day first, optional job
 * filter); tapping a row hands that job+date to the Labour tab (via the
 * labourEditTarget store) where the existing pre-seeded tick screen
 * does the actual editing. A per-row delete covers the remove case
 * without a round-trip through the tick screen.
 *
 * Roles: viewable by BOTH roles — rows carry attendance identity only
 * (worker / job / date / fraction / times), never rates or cost. The
 * delete button renders only where the backend rule would allow it
 * (admin: any; contributor: own entries, today only) and the server
 * stays authoritative regardless.
 */

type Section = { title: string; data: LabourEntryPublic[] };

export default function LabourRecordsScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const me = useMe();
  const jobs = useJobs();
  const workers = useWorkers(true);
  const setTarget = useLabourEditTargetStore((st) => st.setTarget);

  const [monthAnchor, setMonthAnchor] = useState<string>(() =>
    monthStart(todayISO()),
  );
  const [jobFilter, setJobFilter] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const from = monthStart(monthAnchor);
  const to = monthEnd(monthAnchor);
  const entries = useLabourEntriesRange(jobFilter, from, to);
  const del = useDeleteLabourEntry();

  const isAdmin = me.data?.role === 'admin';
  const myId = me.data?.user_id;
  const today = todayISO();

  const jobNameFor = (jobId: string): string =>
    jobs.data?.find((j) => j.job_id === jobId)?.job_name ?? '—';
  const workerNameFor = (workerId: string): string =>
    workers.data?.find((w) => w.worker_id === workerId)?.display_name ?? '—';

  const jobOptions = useMemo(
    () => [
      { value: null, label: t('labour.filter_all_jobs') },
      ...(jobs.data ?? [])
        .filter((j) => j.status === 'active')
        .map((j) => ({ value: j.job_id, label: j.job_name })),
    ],
    [jobs.data, t],
  );

  const sections = useMemo<Section[]>(() => {
    const byDate = new Map<string, LabourEntryPublic[]>();
    for (const e of entries.data ?? []) {
      const list = byDate.get(e.work_date) ?? [];
      list.push(e);
      byDate.set(e.work_date, list);
    }
    return [...byDate.entries()]
      .sort((a, b) => (a[0] < b[0] ? 1 : -1))
      .map(([date, data]) => ({ title: date, data }));
  }, [entries.data]);

  const canDelete = (e: LabourEntryPublic): boolean =>
    isAdmin || (e.recorded_by_user_id === myId && e.work_date >= today);

  const onEditDay = (e: LabourEntryPublic) => {
    // Hand the job+date to the Labour tab's tick screen — the existing
    // pre-seed + diff-save flow IS the editor.
    setTarget({ jobId: e.job_id, date: e.work_date });
    router.push('/(tabs)/labour' as unknown as Href);
  };

  const onDelete = (e: LabourEntryPublic) => {
    Alert.alert(
      t('labour.record_delete_title'),
      t('labour.record_delete_message', {
        name: workerNameFor(e.worker_id),
        date: formatDateAU(e.work_date),
      }),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('labour.record_delete_cta'),
          style: 'destructive',
          onPress: () => {
            del.mutate(
              { entryId: e.entry_id },
              {
                onError: () =>
                  Alert.alert(t('common.error'), t('labour.record_delete_error')),
              },
            );
          },
        },
      ],
    );
  };

  const onBack = () => {
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/labour' as unknown as Href);
  };

  const fractionLabel = (e: LabourEntryPublic): string => {
    const frac = Number(e.day_fraction);
    const fracText =
      frac === 1
        ? t('labour.full_day')
        : frac === 0.5
          ? t('labour.half_day')
          : String(frac);
    const start = hhmmFromServer(e.start_time);
    const end = hhmmFromServer(e.end_time);
    if (start && end) return `${fracText} · ${start}–${end}`;
    if (e.hours != null) {
      return `${fracText} · ${formatHoursShort(Number(e.hours))}`;
    }
    return fracText;
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          accessibilityRole="button"
          testID="records-back"
          style={({ pressed }) => [s.backBtn, pressed && s.pressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('labour.records_title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      <View style={s.controls}>
        <View style={s.monthRow}>
          <TouchableOpacity
            onPress={() => setMonthAnchor((m) => shiftMonthISO(m, -1))}
            style={s.chevronBtn}
            accessibilityRole="button"
            testID="records-prev-month"
          >
            <Text style={s.chevronText}>{'‹'}</Text>
          </TouchableOpacity>
          <Text style={s.monthLabel}>
            {formatMonthLabel(monthAnchor, i18n.language)}
          </Text>
          <TouchableOpacity
            onPress={() => setMonthAnchor((m) => shiftMonthISO(m, 1))}
            style={s.chevronBtn}
            accessibilityRole="button"
            testID="records-next-month"
          >
            <Text style={s.chevronText}>{'›'}</Text>
          </TouchableOpacity>
        </View>
        <TouchableOpacity
          onPress={() => setPickerOpen(true)}
          style={s.filterChip}
          accessibilityRole="button"
          testID="records-job-filter"
        >
          <Text style={s.filterChipText} numberOfLines={1}>
            {jobFilter ? jobNameFor(jobFilter) : t('labour.filter_all_jobs')}
          </Text>
        </TouchableOpacity>
        <Text style={s.editHint}>{t('labour.records_edit_hint')}</Text>
      </View>

      {entries.isLoading ? (
        <View style={s.state} testID="records-loading">
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : entries.isError ? (
        <View style={s.state} testID="records-error">
          <Text style={s.stateText}>{t('common.error')}</Text>
          <Pressable
            onPress={() => void entries.refetch()}
            accessibilityRole="button"
          >
            <Text style={s.retryText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(e) => e.entry_id}
          refreshControl={
            <RefreshControl
              refreshing={entries.isRefetching}
              onRefresh={() => void entries.refetch()}
              tintColor="#1e293b"
            />
          }
          renderSectionHeader={({ section }) => (
            <Text style={s.sectionHeader}>
              {formatDateAU(section.title)}
            </Text>
          )}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => onEditDay(item)}
              accessibilityRole="button"
              testID={`record-row-${item.entry_id}`}
              style={({ pressed }) => [s.row, pressed && s.pressed]}
            >
              <View style={s.rowMain}>
                <Text style={s.rowWorker}>
                  {workerNameFor(item.worker_id)}
                </Text>
                <Text style={s.rowMeta} numberOfLines={1}>
                  {jobFilter ? fractionLabel(item) : `${jobNameFor(item.job_id)} · ${fractionLabel(item)}`}
                </Text>
              </View>
              {canDelete(item) ? (
                <TouchableOpacity
                  onPress={() => onDelete(item)}
                  disabled={del.isPending}
                  hitSlop={8}
                  accessibilityRole="button"
                  testID={`record-delete-${item.entry_id}`}
                  style={s.deleteBtn}
                >
                  <Text style={s.deleteBtnText}>
                    {t('labour.record_delete_cta')}
                  </Text>
                </TouchableOpacity>
              ) : null}
            </Pressable>
          )}
          ListEmptyComponent={
            <View style={s.state} testID="records-empty">
              <Text style={s.stateText}>{t('labour.records_empty')}</Text>
            </View>
          }
          contentContainerStyle={s.listContent}
          stickySectionHeadersEnabled={false}
        />
      )}

      <OptionPickerModal
        visible={pickerOpen}
        title={t('labour.job_picker_title')}
        options={jobOptions}
        selected={jobFilter}
        onSelect={(value) => {
          setJobFilter(value);
          setPickerOpen(false);
        }}
        onClose={() => setPickerOpen(false)}
        cancelLabel={t('common.cancel')}
      />
    </SafeAreaView>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  backBtn: { flexDirection: 'row', alignItems: 'center', minWidth: 70 },
  backChevron: { fontSize: 26, color: '#1e293b', marginRight: 2 },
  backLabel: { fontSize: 15, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { minWidth: 70 },
  controls: { paddingHorizontal: 16, paddingTop: 12, gap: 8 },
  monthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  chevronBtn: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
  },
  chevronText: { fontSize: 18, color: '#1e293b' },
  monthLabel: { fontSize: 16, fontWeight: '600', color: '#0f172a' },
  filterChip: {
    alignSelf: 'flex-start',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
    maxWidth: '80%',
  },
  filterChipText: { fontSize: 14, color: '#0f172a', fontWeight: '500' },
  editHint: { fontSize: 12, color: '#64748b' },
  state: { alignItems: 'center', padding: 24, gap: 10 },
  stateText: { color: '#64748b', fontSize: 15, textAlign: 'center' },
  retryText: { color: '#2563eb', fontSize: 14, fontWeight: '500' },
  listContent: { paddingBottom: 24, paddingHorizontal: 16 },
  sectionHeader: {
    fontSize: 13,
    fontWeight: '600',
    color: '#475569',
    backgroundColor: '#f8fafc',
    paddingVertical: 6,
    paddingHorizontal: 8,
    marginTop: 12,
    borderRadius: 6,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  rowMain: { flex: 1, gap: 2 },
  rowWorker: { fontSize: 15, color: '#0f172a', fontWeight: '500' },
  rowMeta: { fontSize: 13, color: '#64748b' },
  deleteBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
  },
  deleteBtnText: { color: '#b91c1c', fontSize: 13, fontWeight: '500' },
  pressed: { opacity: 0.6 },
});
