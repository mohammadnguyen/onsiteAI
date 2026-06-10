import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useFailuresStore, type CaptureFailure } from '../store/failures';

/**
 * M0: persistent failed-capture list.
 *
 * Rendered on the Capture screen between the submit button and the
 * "My Captures" list. Shows entries from the persisted failures store
 * (survives form reset and app restart). Tapping a row puts the
 * original text back into the capture form via `onRefill` so the user
 * can retry without retyping; each row has its own dismiss control and
 * the whole list can be cleared at once.
 *
 * Rows with context 'app' come from the global JS error hook / error
 * boundary — they carry no capture text and are not refillable.
 *
 * Renders nothing when the store is empty, so the happy-path capture
 * screen is visually unchanged.
 */

type Props = {
  onRefill: (text: string) => void;
};

const PREVIEW_MAX = 60;

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max).trimEnd() + '…';
}

/** DD/MM HH:mm — matches the app's AU date convention, adds time. */
function formatTs(ts: number): string {
  const d = new Date(ts);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${dd}/${mm} ${hh}:${mi}`;
}

export function RecentFailuresList({ onRefill }: Props) {
  const { t } = useTranslation();
  const failures = useFailuresStore((st) => st.failures);
  const dismissFailure = useFailuresStore((st) => st.dismissFailure);
  const clearFailures = useFailuresStore((st) => st.clearFailures);

  if (failures.length === 0) return null;

  return (
    <View style={s.section} testID="failures-section">
      <View style={s.headerRow}>
        <Text style={s.heading}>{t('failures.title')}</Text>
        <Pressable
          onPress={clearFailures}
          testID="failures-clear"
          accessibilityRole="button"
          hitSlop={8}
        >
          <Text style={s.clearAll}>{t('failures.clear_all')}</Text>
        </Pressable>
      </View>
      {failures.map((f) => (
        <FailureRow
          key={f.id}
          failure={f}
          onRefill={onRefill}
          onDismiss={dismissFailure}
        />
      ))}
    </View>
  );
}

function FailureRow({
  failure,
  onRefill,
  onDismiss,
}: {
  failure: CaptureFailure;
  onRefill: (text: string) => void;
  onDismiss: (id: string) => void;
}) {
  const { t } = useTranslation();
  const refillable = failure.context !== 'app' && failure.inputText.length > 0;

  return (
    <Pressable
      testID={`failure-row-${failure.id}`}
      accessibilityRole={refillable ? 'button' : undefined}
      disabled={!refillable}
      onPress={() => refillable && onRefill(failure.inputText)}
      style={({ pressed }) => [s.row, pressed && refillable && s.rowPressed]}
    >
      <View style={s.rowBody}>
        <Text style={s.errorText} numberOfLines={2}>
          {failure.context === 'app'
            ? `${t('failures.app_error')}: ${failure.errorMessage}`
            : failure.errorMessage}
        </Text>
        {failure.inputText ? (
          <Text style={s.inputText} numberOfLines={1}>
            {truncate(failure.inputText, PREVIEW_MAX)}
          </Text>
        ) : null}
        <Text style={s.meta}>
          {formatTs(failure.ts)}
          {refillable ? ` · ${t('failures.refill_hint')}` : ''}
        </Text>
      </View>
      <Pressable
        onPress={() => onDismiss(failure.id)}
        testID={`failure-dismiss-${failure.id}`}
        accessibilityRole="button"
        accessibilityLabel={t('failures.dismiss')}
        hitSlop={10}
        style={s.dismissBtn}
      >
        <Text style={s.dismissText}>✕</Text>
      </Pressable>
    </Pressable>
  );
}

const s = StyleSheet.create({
  section: {
    marginTop: 20,
    borderWidth: 1,
    borderColor: '#fecaca',
    backgroundColor: '#fef2f2',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 2,
  },
  heading: { fontSize: 15, fontWeight: '600', color: '#991b1b' },
  clearAll: { fontSize: 13, color: '#b91c1c', fontWeight: '500' },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: '#fecaca',
  },
  rowPressed: { opacity: 0.6 },
  rowBody: { flex: 1, paddingRight: 8 },
  errorText: { color: '#991b1b', fontSize: 13, fontWeight: '500' },
  inputText: { color: '#334155', fontSize: 13, marginTop: 3 },
  meta: { color: '#94a3b8', fontSize: 11, marginTop: 3 },
  dismissBtn: { padding: 2 },
  dismissText: { color: '#b91c1c', fontSize: 14, fontWeight: '600' },
});
