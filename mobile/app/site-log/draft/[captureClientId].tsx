import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';

import { useMe } from '../../../src/api/hooks/useAuth';
import { BackLink } from '../../../src/siteLog/BackLink';
import { confirmDestructive, notify } from '../../../src/siteLog/dialogs';
import { runSubmit } from '../../../src/siteLog/submit';
import { useAuthStore } from '../../../src/store/auth';
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
  // Works offline, and is no weaker: the id comes from the token the server
  // issued to this session.
  const tokenUserId = useAuthStore((s) => s.userId);
  const userId = me?.user_id ?? tokenUserId;
  const qc = useQueryClient();
  const found = store.get(String(captureClientId));
  // Drafts survive an involuntary logout on purpose, so the next person to
  // sign in on a shared phone must not be able to read - let alone resume -
  // what the previous one wrote. Ownership is checked here, not only in the
  // list that happens to filter.
  const draft = found && userId && found.user_id === userId ? found : undefined;
  const [busy, setBusy] = useState(false);
  // Whether THIS capture is being sent, by any screen. `busy` belongs to
  // this instance, and navigating away and back gives a fresh one while the
  // submission it started is still running.
  const sending = store.submitting.includes(String(captureClientId));
  const [banner, setBanner] = useState<string | null>(null);
  // A submission outlives this screen; navigating after the user has moved
  // on would replace whatever they opened next.
  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  const resume = useCallback(async () => {
    if (!draft || !userId) return;
    // The session this resume was started in - read before any await.
    const sessionNonce = useAuthStore.getState().sessionNonce;
    setBusy(true);
    setBanner(null);
    store.beginSubmit(draft.capture_client_id);
    let outcome;
    try {
      outcome = await runSubmit({
        draft,
        userId,
        sessionNonce,
        patch: (p) => store.patchDurable(draft.capture_client_id, p),
      });
    } catch {
      // A progress write to storage rejected. The draft is still on disk;
      // say so rather than leaving a spinner and a dead button.
      setBanner(t('siteLog.error.draft_save_failed'));
      return;
    } finally {
      store.endSubmit(draft.capture_client_id);
      setBusy(false);
    }

    // Bookkeeping first and unconditionally - see new.tsx for why.
    if (outcome.kind !== 'error') {
      qc.invalidateQueries({ queryKey: ['site-log', 'mine'] });
    }
    if (outcome.kind === 'complete') {
      await store.removeAndRelease(draft.capture_client_id);
    }

    // Only now, and only if this screen is still in front of the user.
    if (!mountedRef.current) return;

    if (outcome.kind === 'complete') {
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
    notify({
      title: t('siteLog.status.created_title'),
      body: [
        outcome.kind === 'partial'
          ? t('siteLog.status.partial_body')
          : t(outcome.bodyKey),
        outcome.limitation ? t(outcome.limitation) : null,
      ]
        .filter(Boolean)
        .join('\n\n'),
      okLabel: t('common.ok'),
      onOk: () => {
        if (!mountedRef.current) return;
        router.replace(`/site-log/${outcome.event.site_log_event_id}` as never);
      },
    });
  }, [draft, qc, store, t, userId]);

  const discard = useCallback(() => {
    if (!draft) return;
    // Checked again here, not only through the disabled button: the store
    // is the only thing that knows about a submission started elsewhere.
    if (store.submitting.includes(draft.capture_client_id)) return;
    confirmDestructive({
      title: t('siteLog.draft.discard_title'),
      body: t('siteLog.draft.discard_body'),
      confirmLabel: t('siteLog.draft.discard_confirm'),
      cancelLabel: t('common.cancel'),
      onConfirm: () => {
        // Explicitly discarded by the user: the kept files go with it.
        void store.removeAndRelease(draft.capture_client_id);
        router.back();
      },
    });
  }, [draft, store, t]);

  if (!draft) {
    return (
      <SafeAreaView style={s.safe} edges={['top']}>
        <BackLink fallback="/site-log" />
        <Text style={s.empty}>{t('siteLog.draft.not_found')}</Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body}>
        <BackLink fallback="/site-log" />
        <Text style={s.h1}>{t('siteLog.draft.title')}</Text>

        {/* Three different things, never conflated: the save result is
            unknown; the server already holds the record and something about
            it is unfinished; or nothing has been sent at all. */}
        {draft.unconfirmed ? (
          <Text style={s.unconfirmed}>{t('siteLog.status.unconfirmed')}</Text>
        ) : draft.server ? (
          <Text style={s.meta}>
            {t('siteLog.draft.record_exists', {
              status: t(`siteLog.status.${draft.server.capture_status}`),
            })}
          </Text>
        ) : (
          <Text style={s.meta}>{t('siteLog.list.draft_not_sent')}</Text>
        )}

        {draft.last_message && draft.last_message !== 'siteLog.status.unconfirmed' ? (
          // Skipped when it would repeat the banner above word for word.
          <Text style={s.warnNote}>{t(draft.last_message)}</Text>
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

        <PrimaryButton
          label={t('siteLog.draft.resume')}
          onPress={resume}
          disabled={busy || sending}
        />
        {sending && !busy ? (
          <Text style={s.meta}>{t('siteLog.draft.sending_elsewhere')}</Text>
        ) : null}
        {/* Not while a submission is running: discarding would delete the
            recovery information and the files it is still using, without
            stopping it. */}
        <Pressable onPress={discard} style={s.discard} disabled={busy || sending}>
          <Text style={busy || sending ? s.discardDisabled : s.discardText}>
            {t('siteLog.draft.discard')}
          </Text>
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
  discardDisabled: { color: tokens.muted },
  empty: { color: tokens.muted, textAlign: 'center', marginTop: 48 },
});
