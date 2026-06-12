import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import type { WorkerPublic } from '../api/hooks/useLabour';

/**
 * L-B1: the daily attendance tick list.
 *
 * Purely presentational — the Labour screen owns all state (tick map,
 * fractions, lock computation) and passes resolved rows down. Rendered
 * with a plain map inside the screen's ScrollView (no FlatList: rosters
 * are small for a single-builder tenant, and nesting a VirtualizedList
 * in a ScrollView is an RN anti-pattern).
 *
 * Row anatomy: checkbox + name (+ optional note / deactivated badge /
 * lock reason) and, when ticked, the Full/Half day-fraction pills.
 * Locked rows (another user's entry, or a past-date entry the caller
 * may not change) render muted with the reason and ignore presses.
 */

export type ChecklistRowState = {
  worker: WorkerPublic;
  ticked: boolean;
  /** Effective day fraction (0.5 | 1) — only meaningful while ticked. */
  fraction: number;
  /** True when the current user may not modify the existing entry. */
  locked: boolean;
  /** Translated lock copy (name-free); null when not locked. */
  lockReason: string | null;
};

export function WorkerChecklist({
  rows,
  disabled,
  onToggle,
  onSetFraction,
}: {
  rows: ChecklistRowState[];
  disabled: boolean;
  onToggle: (workerId: string) => void;
  onSetFraction: (workerId: string, fraction: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <View style={s.list} testID="worker-checklist">
      {rows.map((row) => {
        const id = row.worker.worker_id;
        const rowDisabled = disabled || row.locked;
        return (
          <View
            key={id}
            style={[s.row, row.locked && s.rowLocked]}
            testID={`worker-row-${id}`}
          >
            <TouchableOpacity
              style={s.main}
              onPress={() => onToggle(id)}
              disabled={rowDisabled}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: row.ticked, disabled: rowDisabled }}
              accessibilityLabel={row.worker.display_name}
              testID={`worker-toggle-${id}`}
            >
              <View style={[s.checkbox, row.ticked && s.checkboxChecked]}>
                {row.ticked ? <Text style={s.checkmark}>{'✓'}</Text> : null}
              </View>
              <View style={s.nameBlock}>
                <View style={s.nameLine}>
                  <Text style={s.name} numberOfLines={1}>
                    {row.worker.display_name}
                  </Text>
                  {!row.worker.is_active ? (
                    <Text style={s.badge}>{t('labour.deactivated_badge')}</Text>
                  ) : null}
                </View>
                {row.worker.note ? (
                  <Text style={s.note} numberOfLines={1}>
                    {row.worker.note}
                  </Text>
                ) : null}
                {row.lockReason ? (
                  <Text style={s.lockText}>{row.lockReason}</Text>
                ) : null}
              </View>
            </TouchableOpacity>
            {row.ticked ? (
              <View style={s.pills}>
                <FractionPill
                  label={t('labour.full_day')}
                  active={row.fraction === 1}
                  disabled={rowDisabled}
                  onPress={() => onSetFraction(id, 1)}
                  testID={`fraction-full-${id}`}
                />
                <FractionPill
                  label={t('labour.half_day')}
                  active={row.fraction === 0.5}
                  disabled={rowDisabled}
                  onPress={() => onSetFraction(id, 0.5)}
                  testID={`fraction-half-${id}`}
                />
              </View>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

function FractionPill({
  label,
  active,
  disabled,
  onPress,
  testID,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[s.pill, active && s.pillActive, disabled && s.pillDisabled]}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
      testID={testID}
    >
      <Text style={[s.pillText, active && s.pillTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  list: {
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    backgroundColor: '#ffffff',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
  },
  rowLocked: { opacity: 0.55 },
  main: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    gap: 10,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderWidth: 1.5,
    borderColor: '#94a3b8',
    borderRadius: 4,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxChecked: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  checkmark: { color: '#ffffff', fontSize: 14, fontWeight: '700' },
  nameBlock: { flex: 1 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  name: { color: '#0f172a', fontSize: 16, fontWeight: '500', flexShrink: 1 },
  badge: {
    color: '#92400e',
    backgroundColor: '#fef3c7',
    fontSize: 11,
    fontWeight: '600',
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: 8,
    overflow: 'hidden',
  },
  note: { color: '#64748b', fontSize: 13, marginTop: 1 },
  lockText: { color: '#92400e', fontSize: 12, marginTop: 2 },
  pills: { flexDirection: 'row', gap: 6 },
  pill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  pillActive: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  pillDisabled: { opacity: 0.5 },
  pillText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  pillTextActive: { color: '#ffffff' },
});
