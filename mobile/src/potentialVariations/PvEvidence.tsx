import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';

import type { PvDetailOut } from '../api/potentialVariations';
import { tokens } from '../ui/tokens';
import { attachmentCounts } from './status';

/**
 * What a flag points at (DEC-PV-001 evidence rules).
 *
 * The flag pins the entry version the person saw: that version's text is
 * shown as the original record. Attachments belong to the ENTRY, not to a
 * version, so they are summarised as the entry's list NOW - never as a
 * snapshot of that version - and are opened through the entry, under the
 * entry's own rules. A newer version, or a withdrawal since, is said
 * plainly.
 *
 * When this user may not read the entry the server sends nothing taken
 * from it, and nothing taken from it is shown: no text, no attachment
 * summary, no way in.
 */
export function PvEvidence({ pv }: { pv: PvDetailOut }) {
  const { t } = useTranslation();
  const src = pv.source;
  if (!src.readable) {
    return (
      <Text style={s.hidden} testID="pv-source-hidden">
        {t('potentialVariations.detail.source_hidden')}
      </Text>
    );
  }

  const pinned = src.pinned;
  const newer = src.current_revision_no !== null && src.current_revision_no > src.revision_no;
  const attachments = src.current_attachments ?? [];
  const counts = attachmentCounts(attachments);
  const notAllSaved = attachments.some((a) => a.state !== 'stored');
  const summary = counts
    .map(([media, n]) => t(`siteLog.list.count_${media}`, { count: n }))
    .join(t('potentialVariations.detail.list_separator'));

  return (
    <View style={s.wrap} testID="pv-evidence">
      <Text style={s.caption} testID="pv-pinned-caption">
        {t('potentialVariations.detail.original', {
          n: src.revision_no,
          time: pinned ? new Date(pinned.created_at).toLocaleString() : '—',
        })}
      </Text>
      <View style={s.box}>
        {pinned?.body_text ? (
          <Text style={s.text} testID="pv-pinned-text">
            {pinned.body_text}
          </Text>
        ) : (
          <Text style={s.empty}>{t('potentialVariations.new.no_text')}</Text>
        )}
        {pinned?.internal_location ? (
          <Text style={s.meta}>
            {t('potentialVariations.detail.location', { place: pinned.internal_location })}
          </Text>
        ) : null}
      </View>
      {newer ? (
        <Text style={s.warn} testID="pv-newer-version">
          {t('potentialVariations.detail.newer_version', { n: src.current_revision_no })}
        </Text>
      ) : null}
      {src.current_withdrawn ? (
        <Text style={s.warn} testID="pv-entry-withdrawn">
          {t('potentialVariations.detail.withdrawn')}
        </Text>
      ) : null}
      {counts.length > 0 ? (
        <Text style={s.meta} testID="pv-attachments">
          {t('potentialVariations.detail.attachments_now', { list: summary })}
          {notAllSaved ? ` · ${t('potentialVariations.detail.attachments_not_all_saved')}` : ''}
        </Text>
      ) : null}
      <Pressable
        onPress={() => router.push(`/site-log/${src.site_log_event_id}` as never)}
        accessibilityRole="button"
        hitSlop={8}
        testID="pv-open-entry"
      >
        <Text style={s.link}>{t('potentialVariations.detail.open_entry')}</Text>
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 8 },
  caption: { color: tokens.ink3, fontSize: 12, fontWeight: '600' },
  box: {
    gap: 6,
    backgroundColor: tokens.surface,
    borderColor: tokens.line,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  text: { color: tokens.ink, fontSize: 15 },
  empty: { color: tokens.muted },
  meta: { color: tokens.ink3, fontSize: 13 },
  warn: { color: tokens.warnMid, fontSize: 13 },
  hidden: { color: tokens.ink3, fontSize: 15 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
});
