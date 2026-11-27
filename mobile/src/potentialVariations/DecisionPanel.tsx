import { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import {
  PV_END_REASONS,
  PV_NOTE_MAX_CHARS,
  PV_REVIEW_STATES,
  type PvDecision,
  type PvEndReason,
  type PvOut,
  type PvReviewState,
} from '../api/potentialVariations';
import { Chip, PrimaryButton } from '../ui/kit';
import { tokens } from '../ui/tokens';

type Draft = { state: PvReviewState; reason: PvEndReason | null; note: string };

/** Characters as the server counts them (code points), after trimming. */
const noteLength = (note: string) => Array.from(note.trim()).length;

/**
 * The builder's decision on a flag (DEC-PV-001): To check, Following up, or
 * Follow-up ended - ending keeps a reason, and "Other" needs a note. Shown
 * only when the server says this user may decide (`permissions.decide`).
 *
 * State, reason and note are ONE decision on the server: each save replaces
 * all three. So what is saved is exactly what is on screen - the note field
 * starts with the current note, and clearing it is a visible act - and
 * nothing is sent until Save. None of the three states means approved,
 * priced or authorised.
 */
export function DecisionPanel({
  pv,
  disabled,
  onDecide,
}: {
  pv: PvOut;
  disabled: boolean;
  /** Resolves true once the server has the decision. */
  onDecide: (decision: PvDecision) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft | null>(null);

  const choose = (state: PvReviewState) =>
    setDraft((d) => ({
      state,
      // A reason belongs to an ended follow-up only.
      reason: state === 'ended' ? (d?.reason ?? (pv.review_state === 'ended' ? pv.ended_reason : null)) : null,
      note: d?.note ?? pv.decision_note ?? '',
    }));

  const shown = draft?.state ?? pv.review_state;
  const chips = (
    <View style={s.chips} accessibilityRole="radiogroup">
      {PV_REVIEW_STATES.map((state) => (
        <Chip
          key={state}
          label={t(`potentialVariations.decision.choose_${state}`)}
          selected={state === shown}
          disabled={disabled}
          accessibilityRole="radio"
          onPress={() => !disabled && choose(state)}
          testID={`pv-decide:${state}`}
        />
      ))}
    </View>
  );
  if (!draft) return <View style={s.wrap}>{chips}</View>;

  const ending = draft.state === 'ended';
  const length = noteLength(draft.note);
  const tooLong = length > PV_NOTE_MAX_CHARS;
  const needsReason = ending && draft.reason === null;
  const needsNote = ending && draft.reason === 'other' && length === 0;
  const decision: PvDecision = {
    review_state: draft.state,
    ended_reason: ending ? draft.reason : null,
    note: draft.note.trim() || null,
  };
  const unchanged =
    decision.review_state === pv.review_state &&
    (decision.ended_reason ?? null) === pv.ended_reason &&
    decision.note === pv.decision_note;
  const canSave = !disabled && !needsReason && !needsNote && !tooLong && !unchanged;

  return (
    <View style={s.wrap} testID="pv-decision-editor">
      {chips}
      {ending ? (
        <>
          <Text style={s.label}>{t('potentialVariations.decision.reason_label')}</Text>
          <View style={s.chips} accessibilityRole="radiogroup">
            {PV_END_REASONS.map((reason) => (
              <Chip
                key={reason}
                label={t(`potentialVariations.reason.${reason}`)}
                selected={draft.reason === reason}
                disabled={disabled}
                accessibilityRole="radio"
                onPress={() => setDraft({ ...draft, reason })}
                testID={`pv-reason:${reason}`}
              />
            ))}
          </View>
        </>
      ) : null}
      <Text style={s.label}>
        {draft.reason === 'other' && ending
          ? t('potentialVariations.decision.note_required_label')
          : t('potentialVariations.decision.note_label')}
      </Text>
      <TextInput
        value={draft.note}
        onChangeText={(note) => setDraft({ ...draft, note })}
        editable={!disabled}
        multiline
        placeholder={t('potentialVariations.decision.note_placeholder')}
        placeholderTextColor={tokens.muted}
        style={[s.input, tooLong ? s.inputError : null]}
        testID="pv-decision-note"
      />
      <Text style={tooLong || needsNote ? s.error : s.hint}>
        {tooLong
          ? t('potentialVariations.decision.note_too_long', { max: PV_NOTE_MAX_CHARS })
          : needsNote
            ? t('potentialVariations.decision.note_needed')
            : t('potentialVariations.decision.note_hint', { max: PV_NOTE_MAX_CHARS })}
      </Text>
      <PrimaryButton
        label={t('potentialVariations.decision.save')}
        onPress={() => {
          if (!canSave) return;
          void onDecide(decision).then((saved) => {
            if (saved) setDraft(null);
          });
        }}
        disabled={!canSave}
        testID="pv-decision-save"
      />
      <Pressable
        onPress={() => setDraft(null)}
        accessibilityRole="button"
        hitSlop={8}
        testID="pv-decision-cancel"
      >
        <Text style={s.cancel}>{t('common.cancel')}</Text>
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  label: { color: tokens.ink2, fontWeight: '600', marginTop: 4 },
  input: {
    minHeight: 56,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
    backgroundColor: tokens.surface,
    color: tokens.ink,
    fontSize: 16,
    textAlignVertical: 'top',
  },
  inputError: { borderColor: tokens.bad },
  hint: { color: tokens.ink3, fontSize: 12 },
  error: { color: tokens.bad, fontSize: 12 },
  cancel: { color: tokens.ink3, fontWeight: '600', fontSize: 15, textAlign: 'center' },
});
