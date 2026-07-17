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
import { SearchIcon, ChevronRightIcon } from '../ui/icons';

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
  labelFor,
  onSelect,
  onClose,
}: {
  visible: boolean;
  /** Active jobs only — the caller filters status. */
  jobs: JobPublic[];
  /** Display label per job (zh alias when present, else job_name). */
  labelFor: (job: JobPublic) => string;
  onSelect: (jobId: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (q.length === 0) return jobs;
    return jobs.filter((j) => {
      const label = labelFor(j).toLowerCase();
      return (
        label.includes(q) ||
        j.job_name.toLowerCase().includes(q) ||
        (j.job_code ?? '').toLowerCase().includes(q)
      );
    });
  }, [jobs, search, labelFor]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <View style={s.backdrop}>
        <View style={s.panel} testID="job-picker-sheet">
          <View style={s.headerRow}>
            <Text style={s.title}>{t('capture.job_picker_title')}</Text>
            <TouchableOpacity
              onPress={onClose}
              testID="job-picker-close"
              accessibilityRole="button"
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Text style={s.closeText}>{'✕'}</Text>
            </TouchableOpacity>
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
            renderItem={({ item }) => {
              const label = labelFor(item);
              // Secondary line: the English name (when the label is an
              // alias) + the job code — whichever exist.
              const meta = [
                label !== item.job_name ? item.job_name : null,
                item.job_code,
              ]
                .filter(Boolean)
                .join(' · ');
              return (
                <TouchableOpacity
                  onPress={() => onSelect(item.job_id)}
                  style={s.row}
                  testID={`job-picker-row-${item.job_id}`}
                  accessibilityRole="button"
                >
                  <View style={s.rowMain}>
                    <Text style={s.rowLabel}>{label}</Text>
                    {meta.length > 0 ? (
                      <Text style={s.rowMeta}>{meta}</Text>
                    ) : null}
                  </View>
                  <ChevronRightIcon size={15} color={tokens.muted} />
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
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(9, 14, 26, 0.45)',
    justifyContent: 'flex-end',
  },
  panel: {
    backgroundColor: tokens.surface,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 24,
    maxHeight: '80%',
    gap: 12,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: { fontSize: 18, fontWeight: '800', color: tokens.ink },
  closeText: { fontSize: 18, color: tokens.ink2, padding: 4 },
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
    minHeight: 44,
    borderBottomWidth: 1,
    borderBottomColor: tokens.lineSoft,
  },
  rowMain: { flex: 1, minWidth: 0, gap: 2 },
  rowLabel: { fontSize: 16, color: tokens.ink, fontWeight: '600' },
  rowMeta: { fontSize: 13, color: tokens.ink3 },
  empty: { color: tokens.ink3, fontSize: 14, paddingVertical: 16, textAlign: 'center' },
});
