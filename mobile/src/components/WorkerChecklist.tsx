import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { tokens } from '../ui/tokens';
import type { WorkerPublic } from '../api/hooks/useLabour';
import { formatHoursShort, type TimeRangeStatus } from '../util/time';

/**
 * L-B1 / L-C3: the daily attendance tick list.
 *
 * Purely presentational — the Labour screen owns all state (tick map,
 * fractions, typed time text, lock computation) and passes resolved rows
 * down. Rendered with a plain map inside the screen's ScrollView (no
 * FlatList: rosters are small for a single-builder tenant, and nesting a
 * VirtualizedList in a ScrollView is an RN anti-pattern).
 *
 * Row anatomy: checkbox + name (+ optional note / deactivated badge /
 * lock reason) and, when ticked, the Full/Half day-fraction pills plus a
 * TYPED start->end time range (L-C3). The backend derives the hours from
 * that range and stays the source of truth; this row only shows the live
 * computed duration (or an inline validation message) before save.
 * Locked rows render muted with the reason and ignore presses.
 */

export type ChecklistRowState = {
  worker: WorkerPublic;
  ticked: boolean;
  /** Effective day fraction (0.5 | 1) — only meaningful while ticked. */
  fraction: number;
  /** Raw typed start/end text (controlled), only meaningful while ticked. */
  startText: string;
  endText: string;
  /** Derived validity + duration for the typed range (see computeTimeRange). */
  time: TimeRangeStatus;
  /**
   * Existing hours to surface when the entry has hours but NO time range
   * (a pre-L-C3 row), shown read-only so the user can see what drives the
   * cost. Null when not applicable. Hours are NOT sensitive (any auth may
   * see them); only rates/cost are admin-only.
   */
  legacyHours: string | null;
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
  onSetStart,
  onSetEnd,
}: {
  rows: ChecklistRowState[];
  disabled: boolean;
  onToggle: (workerId: string) => void;
  onSetFraction: (workerId: string, fraction: number) => void;
  onSetStart: (workerId: string, text: string) => void;
  onSetEnd: (workerId: string, text: string) => void;
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
              <View style={s.controls}>
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
                <TimeControls
                  id={id}
                  row={row}
                  disabled={rowDisabled}
                  onSetStart={onSetStart}
                  onSetEnd={onSetEnd}
                />
              </View>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

/**
 * The typed start->end time pair plus the live duration / validation
 * line. The hours that drive labour cost are DERIVED server-side from
 * these times — this is display only.
 */
function TimeControls({
  id,
  row,
  disabled,
  onSetStart,
  onSetEnd,
}: {
  id: string;
  row: ChecklistRowState;
  disabled: boolean;
  onSetStart: (workerId: string, text: string) => void;
  onSetEnd: (workerId: string, text: string) => void;
}) {
  const { t } = useTranslation();
  const { time } = row;

  let status: { text: string; error: boolean } | null = null;
  if (time.parseError) {
    status = { text: t('labour.error_time_invalid'), error: true };
  } else if (time.onePresent) {
    status = { text: t('labour.error_time_one'), error: true };
  } else if (time.orderError) {
    status = { text: t('labour.error_time_order'), error: true };
  } else if (time.ready) {
    status = {
      text: t('labour.duration_value', {
        hours: formatHoursShort(time.durationHours),
      }),
      error: false,
    };
  } else if (row.legacyHours) {
    status = {
      text: t('labour.hours_recorded', { hours: row.legacyHours }),
      error: false,
    };
  }

  return (
    <View style={s.timeBlock}>
      <View style={s.timeRow}>
        <TextInput
          value={row.startText}
          onChangeText={(text) => onSetStart(id, text)}
          editable={!disabled}
          keyboardType="numbers-and-punctuation"
          autoCapitalize="none"
          autoCorrect={false}
          placeholder={t('labour.start_placeholder')}
          placeholderTextColor="#94a3b8"
          style={[s.timeInput, disabled && s.pillDisabled]}
          maxLength={8}
          testID={`start-${id}`}
          accessibilityLabel={t('labour.start_label')}
        />
        <Text style={s.timeArrow}>{'→'}</Text>
        <TextInput
          value={row.endText}
          onChangeText={(text) => onSetEnd(id, text)}
          editable={!disabled}
          keyboardType="numbers-and-punctuation"
          autoCapitalize="none"
          autoCorrect={false}
          placeholder={t('labour.end_placeholder')}
          placeholderTextColor="#94a3b8"
          style={[s.timeInput, disabled && s.pillDisabled]}
          maxLength={8}
          testID={`end-${id}`}
          accessibilityLabel={t('labour.end_label')}
        />
      </View>
      {status ? (
        <Text
          style={status.error ? s.timeError : s.timeDuration}
          testID={`time-status-${id}`}
        >
          {status.text}
        </Text>
      ) : null}
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
      // B4.5 review (blocker): do NOT dim the tonal fill — a locked row
      // already carries rowLocked's 0.55, and RN multiplies nested
      // opacity (0.55 x 0.5 = 0.275), which washed the sel background
      // out to white: a saved row stopped showing whether the worker
      // was booked Full or Half. Dim the LABEL only; the fill+border
      // survive. hitSlop restores the tap target the spec's compact
      // padding costs (26px visual -> ~44px touch).
      hitSlop={{ top: 9, bottom: 9, left: 4, right: 4 }}
      style={[s.pill, active && s.pillActive]}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
      testID={testID}
    >
      <Text
        style={[
          s.pillText,
          active && s.pillTextActive,
          disabled && s.pillTextDisabled,
        ]}
      >
        {label}
      </Text>
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
  // B4.5 (design ⑤): ticked = primary blue rounded box; the tick IS
  // the action affordance on this screen, so it keeps the action
  // colour while every SELECTION (Full/Half, dates) goes tonal.
  checkbox: {
    width: 21,
    height: 21,
    borderWidth: 1.5,
    borderColor: '#CBD5E1',
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxChecked: {
    backgroundColor: tokens.primary,
    borderColor: tokens.primary,
  },
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
  controls: { alignItems: 'flex-end', gap: 6 },
  timeBlock: { alignItems: 'flex-end', gap: 2 },
  timeRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  timeInput: {
    width: 64,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 6,
    fontSize: 14,
    color: '#0f172a',
    backgroundColor: '#ffffff',
    textAlign: 'center',
  },
  timeArrow: { color: '#64748b', fontSize: 14 },
  timeDuration: {
    color: '#475569',
    fontSize: 13,
    fontVariant: ['tabular-nums'],
  },
  timeError: { color: '#b45309', fontSize: 12 },
  pills: { flexDirection: 'row', gap: 6 },
  // B4.5: tonal selected state (design ⑤ — Full/Half is a SELECTION).
  pill: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 8,
    backgroundColor: '#ffffff',
  },
  pillActive: { backgroundColor: tokens.sel, borderColor: tokens.selBorder },
  // Still used by the time inputs below (a dimmed TEXT INPUT reads
  // correctly; a dimmed tonal FILL does not — see FractionPill).
  pillDisabled: { opacity: 0.5 },
  pillText: { color: tokens.ink2, fontSize: 11.5, fontWeight: '600' },
  pillTextActive: { color: tokens.selText },
  pillTextDisabled: { color: tokens.ink3 },
});
