import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { useMe } from '../../../src/api/hooks/useAuth';
import { runSubmit } from '../../../src/siteLog/submit';
import { useSiteLogDrafts } from '../../../src/store/siteLogDrafts';
import { PrimaryButton } from '../../../src/ui/kit';
import { tokens } from '../../../src/ui/tokens';

/**
 * A capture that was started but never confirmed saved.
 *
 * Resuming re-sends the SAME declaration under the SAME ids. It never edits
 * what was declared and never creates a second record: the submit routine asks
 * the server what it already holds before doing anything.
 */
export default function ResumeSiteLogDraft() {
  const { t } = useTranslation();
  const { captureClientId } = useLocalSearchParams<{ captureClientId: string }>();
  const store = useSiteLogDrafts();
  const { data: me } = useMe();
  const found = store.get(String(captureClientId));
  // Drafts survive an involuntary logout on purpose, so the next person to
  // sign in on a shared phone must not be able to read - let alone resume -
  // what the previous one wrote. Ownership is checked here, not only in the
  // list that happens to filter.
  const draft = found && me?.user_id && found.user_id === me.user_id ? found : undefined;
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  const resume = useCallback(async () => {
    if (!draft) return;
    setBusy(true);
    setBanner(null);
    if (!me?.user_id) return;
    let outcome;
    try {
      outcome = await runSubmit({
        draft,
        userId: me.user_id,
        patch: (p) => store.patchDurable(draft.capture_client_id, p),
      });
    } finally {
      setBusy(false);
    }

    if (outcome.kind === 'complete') {
      store.remove(draft.capture_client_id);
      router.replace(`/site-log/${outcome.event.site_log_event_id}` as never);
      return;
    }
    if (outcome.kind === 'unconfirmed') {
      setBanner(t('siteLog.status.unconfirmed'));
      return;
    }
    if (outcome.kind === 'error') {
      setBanner(outcome.detail ?? t(outcome.messageKey));
      return;
    }
    Alert.alert(
      t('siteLog.status.created_title'),
      outcome.kind === 'partial'
        ? t('siteLog.status.partial_body')
        : t('siteLog.status.blocked_body'),
      [
        {
          text: t('common.ok'),
          onPress: () =>
            router.replace(`/site-log/${outcome.event.site_log_event_id}` as never),
        },
      ],
    );
  }, [draft, me?.user_id, store, t]);

  const discard = useCallback(() => {
    if (!draft) return;
    Alert.alert(t('siteLog.draft.discard_title'), t('siteLog.draft.discard_body'), [
      { text: t('common.cancel'), style: 'cancel' },
      {
        text: t('siteLog.draft.discard_confirm'),
        style: 'destructive',
        onPress: () => {
          store.remove(draft.capture_client_id);
          router.back();
        },
      },
    ]);
  }, [draft, store, t]);

  if (!draft) {
    return (
      <SafeAreaView style={s.safe} edges={['top']}>
        <Text style={s.empty}>{t('siteLog.draft.not_found')}</Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body}>
        <Text style={s.h1}>{t('siteLog.draft.title')}</Text>

        {draft.unconfirmed ? (
          <Text style={s.unconfirmed}>{t('siteLog.status.unconfirmed')}</Text>
        ) : (
          <Text style={s.meta}>{t('siteLog.list.draft_not_sent')}</Text>
        )}

        {draft.server ? (
          <Text style={s.meta}>
            {t('siteLog.draft.record_exists', {
              status: t(`siteLog.status.${draft.server.capture_status}`),
            })}
          </Text>
        ) : null}

        <Text style={s.text}>{draft.body_text || t('siteLog.list.no_text')}</Text>

        {draft.attachments.map((a) => (
          <View key={a.attachment_client_id} style={s.att}>
            <Text style={s.attName} numberOfLines={1}>
              {a.name}
            </Text>
            <Text style={s.attState}>
              {a.status === 'missing'
                ? t('siteLog.attachment.missing')
                : t(`siteLog.attachment.${a.status}`)}
            </Text>
          </View>
        ))}

        {draft.attachments.some((a) => a.status === 'missing') ? (
          <Text style={s.warnNote}>{t('siteLog.draft.file_gone')}</Text>
        ) : null}
        {draft.attachments.some((a) => a.status === 'pending') ? (
          <Text style={s.warnNote}>{t('siteLog.attachment.pending_note')}</Text>
        ) : null}

        {banner ? <Text style={s.banner}>{banner}</Text> : null}
        {busy ? <ActivityIndicator style={s.spinner} /> : null}

        <PrimaryButton label={t('siteLog.draft.resume')} onPress={resume} disabled={busy} />
        <Pressable onPress={discard} style={s.discard}>
          <Text style={s.discardText}>{t('siteLog.draft.discard')}</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  body: { padding: 16, gap: 10 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  meta: { color: tokens.ink3, fontSize: 12 },
  unconfirmed: {
    color: tokens.warnMid,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
  },
  text: { color: tokens.ink, fontSize: 16, marginTop: 8 },
  att: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
  },
  attName: { flex: 1, color: tokens.ink, marginRight: 12 },
  attState: { color: tokens.ink3, fontSize: 12 },
  warnNote: { color: tokens.warnMid },
  banner: {
    color: tokens.ink,
    backgroundColor: tokens.warnBg,
    padding: 10,
    borderRadius: 8,
  },
  spinner: { marginVertical: 8 },
  discard: { alignSelf: 'center', paddingVertical: 12 },
  discardText: { color: tokens.bad },
  empty: { color: tokens.muted, textAlign: 'center', marginTop: 48 },
});
