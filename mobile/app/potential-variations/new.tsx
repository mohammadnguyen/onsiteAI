import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { useMe } from '../../src/api/hooks/useAuth';
import { useJobs } from '../../src/api/hooks/useJobs';
import { getEvent } from '../../src/api/siteLog';
import { FlagForm } from '../../src/potentialVariations/FlagForm';
import { PendingFlagPanel } from '../../src/potentialVariations/PendingFlagPanel';
import {
  sendPvCreate,
  type PvRefusal,
  type PvSendOutcome,
} from '../../src/potentialVariations/createFlow';
import { usePvLeaveGuard } from '../../src/potentialVariations/usePvLeaveGuard';
import { BackLink } from '../../src/siteLog/BackLink';
import { newCaptureId } from '../../src/siteLog/ids';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { useAuthStore } from '../../src/store/auth';
import {
  usePvPendingCreates,
  usePvPendingHydrated,
  type PendingPvCreate,
} from '../../src/store/pvPendingCreates';
import { isUsableTitle, proposedTitleFrom } from '../../src/tasks/proposedTitle';
import { tokens } from '../../src/ui/tokens';

const WITHDRAWN = 'The source entry is withdrawn';
const SOURCE_NOT_FOUND = 'Site log event not found';

/**
 * Flag one Site Log entry as a potential variation (DEC-PV-001).
 *
 * The entry's current text and version are shown; the title starts as its
 * first line (empty for a voice-only entry) and the user confirms or edits
 * it; the job comes from the entry and is shown, not chosen. Save pins the
 * version on screen at that moment. Saving writes a pending record to the
 * phone first, then sends - so a lost answer is recovered by asking the
 * server, never by sending a second flag under a new key. If the entry has
 * changed meanwhile, the server refuses; the entry is reloaded and the user
 * checks it and saves again. An unresolved flag from this entry is dealt
 * with before a new one can start.
 */
export default function NewPotentialVariationScreen() {
  const { t } = useTranslation();
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const tokenUserId = useAuthStore((st) => st.userId);
  const userId = me?.user_id ?? tokenUserId;
  const activeRef = useScreenActive();
  const { hydrated, loadFailed } = usePvPendingHydrated();
  const pending = usePvPendingCreates((st) =>
    userId && eventId ? st.forEvent(userId, eventId) : undefined,
  );

  const entryKey = ['site-log', 'event', eventId];
  const entry = useQuery({
    queryKey: entryKey,
    queryFn: () => getEvent(String(eventId)),
    enabled: Boolean(eventId),
  });
  const jobs = useJobs();
  const jobName = useMemo(() => {
    const id = entry.data?.job_id;
    if (!id) return null;
    return jobs.data?.find((j) => j.job_id === id)?.job_name ?? t('siteLog.detail.assigned');
  }, [entry.data?.job_id, jobs.data, t]);

  const [title, setTitle] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // The entry is being read again after the server said it had changed.
  const [reloading, setReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per attempt, kept across double taps. A refusal the server gave
  // on purpose starts a new one; an unknown or unavailable result keeps it.
  const keyRef = useRef<string>(newCaptureId());
  // This attempt's record is on the phone: from then on leaving loses nothing.
  const [recorded, setRecorded] = useState(false);
  // Save was tapped; until its record is on the phone, its values are only here.
  const [attempted, setAttempted] = useState(false);

  // The title starts as the entry's first line, once, when the entry loads.
  const proposed = entry.data ? proposedTitleFrom(entry.data.revision.body_text) : null;
  useEffect(() => {
    if (title === null && proposed !== null) setTitle(proposed);
  }, [proposed, title]);

  usePvLeaveGuard({
    edited: title !== null && proposed !== null && title.trim() !== proposed,
    saving: sending,
    recorded,
    attempted,
  });

  /** What a refusal says, in the user's language where it is a known one. */
  const refusalMessage = (r: PvRefusal): string => {
    if (r.sourceChanged) return t('potentialVariations.new.source_changed');
    if (r.status === 409 && r.detail === WITHDRAWN) return t('potentialVariations.new.entry_withdrawn');
    if (r.status === 404 && r.detail === SOURCE_NOT_FOUND) return t('potentialVariations.new.entry_gone');
    return r.detail ?? t('potentialVariations.new.save_failed');
  };

  /** Read the entry again, so the version shown is the one now saved. */
  const reloadEntry = async () => {
    setReloading(true);
    try {
      await qc.invalidateQueries({ queryKey: entryKey, exact: true });
    } finally {
      setReloading(false);
    }
  };

  /** What the screen does with an outcome, once it is still in front. */
  const finish = (outcome: PvSendOutcome) => {
    if (outcome.kind === 'created') {
      router.replace(`/potential-variations/${outcome.pv.potential_variation_id}` as never);
    } else if (outcome.kind === 'refused') {
      setError(refusalMessage(outcome));
    } else if (outcome.kind === 'not_saved') {
      // Nothing was sent; the same key can be used when the user tries again.
      setError(t('potentialVariations.new.not_saved'));
    } else {
      // Unknown or unavailable: the pending record stays - same key, same
      // values - and the panel takes over and says which it is.
      setError(null);
    }
  };

  const onSave = async () => {
    if (sending || reloading || !userId || !eventId || !entry.data) return;
    if (title === null || !isUsableTitle(title)) return;
    // Read before any await: this is the session the user tapped Save in,
    // and the version on screen is the one the user saw.
    const startedUnder = useAuthStore.getState().sessionNonce;
    const shownRevision = entry.data.revision.revision_no;
    setSending(true);
    setAttempted(true);
    setRecorded(false);
    setError(null);
    const record: PendingPvCreate = {
      create_client_id: keyRef.current,
      user_id: userId,
      site_log_event_id: eventId,
      source_revision_no: shownRevision,
      title: title.trim(),
      created_at: Date.now(),
    };
    try {
      const outcome = await sendPvCreate(record, startedUnder, () => setRecorded(true));
      // Another account's session now: nothing here is theirs to update.
      if (outcome.kind === 'stale' || useAuthStore.getState().sessionNonce !== startedUnder) {
        return;
      }
      // The lists must learn of a new flag even if the user has left.
      if (outcome.kind === 'created') {
        void qc.invalidateQueries({ queryKey: ['potential-variations'] });
      }
      if (outcome.kind === 'refused') {
        // Nothing was created under that key: the next save gets a new one.
        keyRef.current = newCaptureId();
        setRecorded(false);
        if (outcome.sourceChanged) void reloadEntry();
      }
      if (activeRef.current) finish(outcome);
    } finally {
      setSending(false);
    }
  };

  if (!eventId) return null;

  let body: ReactNode;
  if (!hydrated && loadFailed) {
    // Whether this entry already has a flag in doubt cannot be known, so no
    // new one is offered until the phone's list can be read.
    body = (
      <View style={s.form}>
        <Text style={s.error}>{t('potentialVariations.new.store_failed')}</Text>
        <Pressable
          onPress={() => void usePvPendingCreates.getState().hydrate()}
          accessibilityRole="button"
          testID="pv-store-retry"
        >
          <Text style={s.retry}>{t('common.retry')}</Text>
        </Pressable>
      </View>
    );
  } else if (!hydrated || entry.isLoading) {
    body = <ActivityIndicator style={s.spinner} />;
  } else if (pending && !sending) {
    // Only a flag NOT being sent from this screen right now: asking the
    // server about this screen's own request would race it.
    body = (
      <PendingFlagPanel
        key={pending.create_client_id}
        pending={pending}
        screenActive={activeRef}
        onRefused={(refusal, values) => {
          // Nothing was created under that key; the next save gets a new
          // one, starting from the title the user had - which, until then,
          // exists only on this screen.
          keyRef.current = newCaptureId();
          setRecorded(false);
          setAttempted(true);
          setTitle(values.title);
          setError(refusalMessage(refusal));
          if (refusal.sourceChanged) void reloadEntry();
        }}
        onDiscarded={() => {
          // The record that made leaving safe is gone.
          setRecorded(false);
          setAttempted(false);
          setError(null);
        }}
      />
    );
  } else if (!entry.data) {
    body = <Text style={s.note}>{t('potentialVariations.new.load_failed')}</Text>;
  } else if (entry.data.revision.withdrawn) {
    body = <Text style={s.note}>{t('potentialVariations.new.entry_withdrawn')}</Text>;
  } else {
    body = (
      <FlagForm
        revision={entry.data.revision}
        jobName={jobName}
        title={title ?? ''}
        onTitleChange={setTitle}
        sending={sending}
        reloading={reloading}
        error={error}
        onSave={() => void onSave()}
      />
    );
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <KeyboardAvoidingView
        style={s.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
          <BackLink fallback={`/site-log/${eventId}`} />
          <Text style={s.h1}>{t('potentialVariations.new.title')}</Text>
          {body}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  flex: { flex: 1 },
  body: { padding: 16, gap: 12 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  form: { gap: 10 },
  error: { color: tokens.bad, fontSize: 13 },
  note: { color: tokens.ink3, marginTop: 16 },
  retry: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  spinner: { marginTop: 32 },
});
