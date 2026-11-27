import { StyleSheet, Text, TextInput, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import type { RevisionOut } from '../api/siteLog';
import { TITLE_MAX_CHARS, isUsableTitle, titleLength } from '../tasks/proposedTitle';
import { PrimaryButton } from '../ui/kit';
import { tokens } from '../ui/tokens';

/**
 * The flag form (DEC-PV-001): what is being flagged - the entry's text as
 * it is now, with its version - the title, the job the flag takes from the
 * entry, and Save. The screen owns every value; this only shows them.
 *
 * The version shown here is the one Save pins: if the entry is reloaded
 * (the server said it had changed), the text and the version change
 * together, and the user checks them before saving again.
 */
export function FlagForm({
  revision,
  jobName,
  title,
  onTitleChange,
  sending,
  reloading,
  error,
  onSave,
}: {
  revision: RevisionOut;
  /** The entry's job, shown not chosen; null when it has none. */
  jobName: string | null;
  title: string;
  onTitleChange: (next: string) => void;
  sending: boolean;
  /** The entry is being read again: Save waits for the version to show. */
  reloading: boolean;
  error: string | null;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const tooLong = titleLength(title) > TITLE_MAX_CHARS;
  return (
    <View style={s.form}>
      <Text style={s.disclaimer} testID="pv-disclaimer">
        {t('potentialVariations.disclaimer')}
      </Text>

      <Text style={s.label}>{t('potentialVariations.new.entry_label')}</Text>
      <View style={s.entry} testID="pv-entry">
        <Text style={s.version} testID="pv-entry-version">
          {t('potentialVariations.new.version', { n: revision.revision_no })}
        </Text>
        {revision.body_text ? (
          <Text style={s.entryText} testID="pv-entry-text">
            {revision.body_text}
          </Text>
        ) : (
          <Text style={s.entryEmpty}>{t('potentialVariations.new.no_text')}</Text>
        )}
      </View>

      <Text style={s.label}>{t('potentialVariations.new.title_label')}</Text>
      <TextInput
        value={title}
        onChangeText={onTitleChange}
        editable={!sending}
        placeholder={t('potentialVariations.new.title_placeholder')}
        placeholderTextColor={tokens.muted}
        multiline
        style={[s.input, tooLong ? s.inputError : null]}
        testID="pv-title"
      />
      <Text style={tooLong ? s.error : s.hint}>
        {tooLong
          ? t('potentialVariations.new.title_too_long', { max: TITLE_MAX_CHARS })
          : t('potentialVariations.new.title_hint')}
      </Text>

      <Text style={s.label}>{t('potentialVariations.new.job')}</Text>
      <Text style={s.value} testID="pv-job">
        {jobName
          ? t('potentialVariations.new.job_from_entry', { job: jobName })
          : t('potentialVariations.new.no_job')}
      </Text>

      {error ? (
        <Text style={s.error} testID="pv-error">
          {error}
        </Text>
      ) : null}
      <PrimaryButton
        label={sending ? t('potentialVariations.new.saving') : t('potentialVariations.new.save')}
        onPress={onSave}
        disabled={sending || reloading || !isUsableTitle(title)}
        testID="pv-save"
      />
    </View>
  );
}

const s = StyleSheet.create({
  form: { gap: 10 },
  disclaimer: {
    color: tokens.ink2,
    backgroundColor: tokens.surfaceSub,
    borderColor: tokens.line,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    fontSize: 13,
  },
  label: { color: tokens.ink2, fontWeight: '600', marginTop: 6 },
  entry: {
    gap: 6,
    backgroundColor: tokens.surface,
    borderColor: tokens.line,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  version: { color: tokens.ink3, fontSize: 12, fontWeight: '600' },
  entryText: { color: tokens.ink, fontSize: 15 },
  entryEmpty: { color: tokens.muted },
  value: { color: tokens.ink, fontSize: 15 },
  input: {
    minHeight: 64,
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
  error: { color: tokens.bad, fontSize: 13 },
});
