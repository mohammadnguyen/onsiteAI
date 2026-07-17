import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useScaledStyles } from '../../ui/type';
import { tokens } from '../../ui/tokens';
import { formatMoney } from '../../util/format';
import type {
  ParseDiagnostics,
} from '../../api/hooks/useParsePreview';
import type { components } from '../../api/types';

type Draft = components['schemas']['ExpenseCreate-Output'];

/**
 * forey F2 (handoff §4.2): live parse chips. As the user types, the
 * preview parser tells us what it recognised — amount / job / supplier
 * show as blue tonal ✓ chips, and a genuine uncertainty (no job match,
 * a suspected duplicate) shows as an amber chip.
 *
 * ONLY renders positive recognitions and real warnings — never a chip
 * per empty field. A blank amount chip would be noise, not signal. The
 * "payment unset" amber hint stays on the payment row where it already
 * lives; this component doesn't duplicate it.
 *
 * `jobName` resolves draft.job_id to the label the rest of capture
 * uses (zh alias → code → name), so a chip echoes exactly what a
 * matched job chip below would say.
 */
export function ParseChips({
  draft,
  diagnostics,
  jobName,
  isSettling,
}: {
  draft: Draft | null;
  diagnostics: ParseDiagnostics | null;
  jobName: (jobId: string) => string;
  isSettling: boolean;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();

  if (!draft || !diagnostics) return null;

  const amount =
    draft.amount_inc_gst != null && draft.amount_inc_gst !== ''
      ? formatMoney(draft.amount_inc_gst)
      : null;
  const jobLabel = draft.job_id ? jobName(draft.job_id) : null;
  const supplier = diagnostics.candidate_supplier_name;
  const duplicate = diagnostics.duplicate_of_expense_id != null;
  // A job was looked for (there is parseable text) but none matched —
  // worth flagging, since a mis-filed expense is the costly mistake.
  const jobUnsure = !draft.job_id && diagnostics.job_conf < 0.5;

  const nothing = !amount && !jobLabel && !supplier && !duplicate && !jobUnsure;
  if (nothing) return null;

  return (
    <View style={[s.row, isSettling && s.settling]} testID="capture-parse-chips">
      {amount ? (
        <Chip kind="ok" text={amount} testID="parse-chip-amount" />
      ) : null}
      {jobLabel ? (
        <Chip kind="ok" text={jobLabel} testID="parse-chip-job" />
      ) : null}
      {supplier ? (
        <Chip kind="ok" text={supplier} testID="parse-chip-supplier" />
      ) : null}
      {jobUnsure ? (
        <Chip
          kind="warn"
          text={t('capture.chip_job_unsure')}
          testID="parse-chip-job-unsure"
        />
      ) : null}
      {duplicate ? (
        <Chip
          kind="warn"
          text={t('capture.chip_duplicate')}
          testID="parse-chip-duplicate"
        />
      ) : null}
    </View>
  );
}

function Chip({
  kind,
  text,
  testID,
}: {
  kind: 'ok' | 'warn';
  text: string;
  testID: string;
}) {
  const s = useScaledStyles(base);
  return (
    <View style={[s.chip, kind === 'ok' ? s.chipOk : s.chipWarn]} testID={testID}>
      <Text style={[s.chipMark, kind === 'ok' ? s.chipMarkOk : s.chipMarkWarn]}>
        {kind === 'ok' ? '✓' : '!'}
      </Text>
      <Text
        style={[s.chipText, kind === 'ok' ? s.chipTextOk : s.chipTextWarn]}
        numberOfLines={1}
      >
        {text}
      </Text>
    </View>
  );
}

const base = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  // Held-but-stale while the next parse is in flight — a hair dimmed so
  // the chips read as "updating" rather than final.
  settling: { opacity: 0.6 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 11,
    paddingVertical: 5,
    maxWidth: 220,
  },
  chipOk: { backgroundColor: tokens.sel, borderColor: tokens.selBorder },
  chipWarn: { backgroundColor: tokens.warnBg, borderColor: tokens.warnBorder },
  chipMark: { fontSize: 12, fontWeight: '800' },
  chipMarkOk: { color: tokens.selText },
  chipMarkWarn: { color: tokens.warnMid },
  chipText: { fontSize: 12.5, fontWeight: '600', flexShrink: 1 },
  chipTextOk: { color: tokens.selText },
  chipTextWarn: { color: tokens.warnMid },
});
