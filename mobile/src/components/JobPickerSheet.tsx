import { useMemo, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  Modal,
  StyleSheet,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { JobPublic } from '../api/hooks/useJobs';
import { tokens } from '../ui/tokens';
import { SearchIcon } from '../ui/icons';

/**
 * O2-A (dogfood feedback #1): searchable job picker for expense capture.
 *
 * Backstop behind the recent-job chips: covers cold-start and
 * many-jobs cases with 1 tap ("More…") + optional search + 1 tap.
 * Search filters on job name, job code, AND the caller-supplied label
 * (which is the zh alias when one exists), so a low-English user can
 * find "工地1" without typing any English.
 *
 * Deliberately shows job IDENTITY only — name/alias + code. No budget,
 * spend, margin, or any money field ever renders here (conservative
 * money-visibility: this sheet is contributor-facing).
 */
export function JobPickerSheet({
  visible,
  jobs,
  recentJobs = [],
  selectedJobId = null,
  labelFor,
  onSelect,
  onClose,
}: {
  visible: boolean;
  /** Active jobs only — the caller filters status. */
  jobs: JobPublic[];
  /** Spec §4.3 最近使用 — the caller's recent-first chips. */
  recentJobs?: JobPublic[];
  /** Currently-selected job: its row shows the blue ✓. */
  selectedJobId?: string | null;
  /** Display label per job (zh alias when present, else job_name). */
  labelFor: (job: JobPublic) => string;
  onSelect: (jobId: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const base = q.length === 0
      ? jobs
      : jobs.filter((j) => {
          const label = labelFor(j).toLowerCase();
          return (
            label.includes(q) ||
            j.job_name.toLowerCase().includes(q) ||
            (j.job_code ?? '').toLowerCase().includes(q)
          );
        });
    // Spec §4.3: 全部项目 · A→Z — locale-aware alphabetical.
    return [...base].sort((a, b) =>
      labelFor(a).localeCompare(labelFor(b), 'zh-Hans-CN-u-co-pinyin'),
    );
  }, [jobs, search, labelFor]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onClose}
    >
      {/* Spec §4.3: FULL-screen selector — 取消 | 选择项目 | 进行中 N. */}
      <View style={s.screen} testID="job-picker-sheet">
        <View style={s.panel}>
          <View style={s.headerRow}>
            <TouchableOpacity
              onPress={onClose}
              testID="job-picker-close"
              accessibilityRole="button"
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Text style={s.cancelText}>{t('common.cancel')}</Text>
            </TouchableOpacity>
            <Text style={s.title}>{t('capture.job_picker_title')}</Text>
            <Text style={s.countText}>
              {t('capture.picker_active', { count: jobs.length })}
            </Text>
          </View>
          <View style={s.searchRow}>
            <SearchIcon size={18} color={tokens.muted} />
            <TextInput
              value={search}
              onChangeText={setSearch}
              placeholder={t('capture.job_picker_search')}
              placeholderTextColor={tokens.muted}
              autoCapitalize="none"
              autoCorrect={false}
              style={s.search}
              testID="job-picker-search"
              accessibilityLabel={t('capture.job_picker_search')}
            />
          </View>
          <FlatList
            data={filtered}
            keyExtractor={(j) => j.job_id}
            keyboardShouldPersistTaps="handled"
            ListHeaderComponent={
              <View>
                {recentJobs.length > 0 && search.trim().length === 0 ? (
                  <View style={s.recentWrap}>
                    <Text style={s.sectionLabel}>
                      {t('capture.picker_recent')}
                    </Text>
                    <View style={s.recentRow}>
                      {recentJobs.map((j) => (
                        <TouchableOpacity
                          key={j.job_id}
                          style={[
                            s.recentChip,
                            selectedJobId === j.job_id && s.recentChipOn,
                          ]}
                          onPress={() => onSelect(j.job_id)}
                          accessibilityRole="button"
                          testID={`job-picker-recent-${j.job_id}`}
                        >
                          <Text
                            style={[
                              s.recentChipText,
                              selectedJobId === j.job_id && s.recentChipTextOn,
                            ]}
                            numberOfLines={1}
                          >
                            {labelFor(j)}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </View>
                ) : null}
                <Text style={s.sectionLabel}>{t('capture.picker_all')}</Text>
              </View>
            }
            renderItem={({ item, index }) => {
              const label = labelFor(item);
              const meta = [
                label !== item.job_name ? item.job_name : null,
                item.job_code,
              ]
                .filter(Boolean)
                .join(' · ');
              const selected = selectedJobId === item.job_id;
              return (
                <TouchableOpacity
                  onPress={() => onSelect(item.job_id)}
                  style={[
                    s.row,
                    index === 0 && s.rowFirst,
                    index === filtered.length - 1 && s.rowLast,
                  ]}
                  testID={`job-picker-row-${item.job_id}`}
                  accessibilityRole="button"
                  accessibilityState={{ selected }}
                >
                  <View style={s.rowMain}>
                    <Text style={s.rowLabel}>{label}</Text>
                    {meta.length > 0 ? (
                      <Text style={s.rowMeta}>{meta}</Text>
                    ) : null}
                  </View>
                  {selected ? (
                    <View style={s.tickCircle}>
                      <Text style={s.tickText}>{'✓'}</Text>
                    </View>
                  ) : null}
                </TouchableOpacity>
              );
            }}
            ListEmptyComponent={
              <Text style={s.empty}>{t('capture.job_picker_empty')}</Text>
            }
            style={s.list}
          />
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: tokens.surfaceSub },
  panel: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 54,
    paddingBottom: 24,
    gap: 12,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: { fontSize: 16, fontWeight: '800', color: tokens.ink },
  cancelText: { fontSize: 15, color: tokens.primary, padding: 4 },
  countText: { fontSize: 12.5, color: tokens.ink3 },
  sectionLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: tokens.muted,
    marginTop: 10,
    marginBottom: 8,
  },
  recentWrap: {},
  recentRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  recentChip: {
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 7,
    maxWidth: 200,
  },
  recentChipOn: { backgroundColor: tokens.sel, borderColor: tokens.selBorder },
  recentChipText: { fontSize: 12.5, fontWeight: '600', color: tokens.ink2 },
  recentChipTextOn: { color: tokens.selText, fontWeight: '700' },
  tickCircle: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: tokens.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tickText: { color: '#ffffff', fontSize: 12, fontWeight: '800' },
  rowFirst: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  rowLast: {
    borderBottomLeftRadius: 16,
    borderBottomRightRadius: 16,
    borderBottomWidth: 0,
  },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    paddingHorizontal: 12,
    height: 46,
    backgroundColor: tokens.surfaceSub,
  },
  search: { flex: 1, fontSize: 16, color: tokens.ink },
  list: { flexGrow: 0 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 13,
    minHeight: 44,
    borderBottomWidth: 1,
    borderBottomColor: tokens.lineSoft,
    backgroundColor: tokens.surface,
  },
  rowMain: { flex: 1, minWidth: 0, gap: 2 },
  rowLabel: { fontSize: 16, color: tokens.ink, fontWeight: '600' },
  rowMeta: { fontSize: 13, color: tokens.ink3 },
  empty: { color: tokens.ink3, fontSize: 14, paddingVertical: 16, textAlign: 'center' },
});
