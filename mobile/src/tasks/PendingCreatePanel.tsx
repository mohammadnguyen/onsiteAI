import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';

import { confirmDestructive } from '../siteLog/dialogs';
import { useAuthStore } from '../store/auth';
import type { PendingTaskCreate } from '../store/taskPendingCreates';
import { formatDateAU } from '../util/dates';
import { PrimaryButton } from '../ui/kit';
import { tokens } from '../ui/tokens';
import { discardPending, resolvePending, sendCreate } from './createFlow';

type Phase = 'checking' | 'not_found' | 'unknown' | 'unavailable' | 'sending';

/**
 * Every await below can outlive the screen or the session it began in. Two
 * different questions follow one: is it still the same account (then what
 * the server said is true, and the task lists must hear of it), and is the
 * user still on this screen (then, and only then, move them or change what
 * the panel says).
 */
function sameSession(startedUnder: number): boolean {
  return useAuthStore.getState().sessionNonce === startedUnder;
}

/**
 * An earlier create from this entry whose answer never came (plan v2 §5).
 *
 * On opening, what happened to it is found out first: if this app still has
 * it out (the user left the screen that was saving it), its own answer is
 * awaited; otherwise the server is ASKED. Found: the task opens and says it
 * was already created. Not found: the saved values are shown with "Send
 * again", which reuses the same key, so even a request that was only slow
 * cannot become a second task. Still unreachable: it says the result is
 * unknown and offers to check again. The server offers no tasks right now
 * (an older or rolled-back backend): it says so, keeps the record as it is
 * and offers to check again - never to send. Discard is offered once nothing
 * is out, and asks.
 */
export function PendingCreatePanel({
  pending,
  screenActive,
  onRefused,
  onDiscarded,
}: {
  pending: PendingTaskCreate;
  /**
   * The SCREEN's focus, not this panel's. Resolving a record removes it,
   * and that unmounts this panel before the result is acted on; the
   * question that matters is whether the user is still on the screen.
   */
  screenActive: { readonly current: boolean };
  /** The server refused the resend on purpose; the record is gone, and its
   *  values are handed back so the user can correct and save them. */
  onRefused: (detail: string | null, values: PendingTaskCreate) => void;
  /** The user discarded the record and it is gone from the phone: the
   *  screen is back to its form, with nothing of that create kept. */
  onDiscarded: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const activeRef = screenActive;
  const [phase, setPhase] = useState<Phase>('checking');
  const started = useRef(false);

  const check = useCallback(async () => {
    const startedUnder = useAuthStore.getState().sessionNonce;
    setPhase('checking');
    const outcome = await resolvePending(pending, startedUnder);
    if (outcome.kind === 'stale' || !sameSession(startedUnder)) return;
    if (outcome.kind === 'found') {
      void qc.invalidateQueries({ queryKey: ['tasks'] });
      if (activeRef.current) {
        router.replace(`/tasks/${outcome.task.task_id}?notice=already_created` as never);
      }
      return;
    }
    if (outcome.kind === 'refused') {
      // The earlier send was still out and the server refused it.
      onRefused(outcome.detail, pending);
      return;
    }
    setPhase(outcome.kind);
  }, [activeRef, onRefused, pending, qc]);

  useEffect(() => {
    // Once per record: the check is a read, but asking twice at mount is noise.
    if (started.current) return;
    started.current = true;
    void check();
  }, [check]);

  const sendAgain = async () => {
    const startedUnder = useAuthStore.getState().sessionNonce;
    setPhase('sending');
    // The SAME record: same key, same values.
    const outcome = await sendCreate(pending, startedUnder);
    if (outcome.kind === 'stale' || !sameSession(startedUnder)) return;
    if (outcome.kind === 'created') {
      void qc.invalidateQueries({ queryKey: ['tasks'] });
      if (activeRef.current) router.replace(`/tasks/${outcome.task.task_id}` as never);
    } else if (outcome.kind === 'refused') {
      onRefused(outcome.detail, pending);
    } else if (outcome.kind === 'not_saved') {
      // Nothing was sent; the record is as it was.
      setPhase('not_found');
    } else if (outcome.kind === 'unavailable') {
      // The routes were gone when it was sent: kept, same key, same values.
      setPhase('unavailable');
    } else {
      setPhase('unknown');
    }
  };

  const discard = () => {
    const startedUnder = useAuthStore.getState().sessionNonce;
    confirmDestructive({
      title: t('tasks.pending.discard_title'),
      body: t('tasks.pending.discard_body'),
      confirmLabel: t('tasks.pending.discard'),
      cancelLabel: t('common.cancel'),
      onConfirm: () => {
        // Answered in the session that asked, or not acted on.
        if (useAuthStore.getState().sessionNonce !== startedUnder) return;
        void discardPending(pending.create_client_id).then((gone) => {
          // Discarding only forgets the record on this phone: a request
          // that reached the server is not cancelled (the dialog says so).
          if (gone && sameSession(startedUnder)) onDiscarded();
        });
      },
    });
  };

  if (phase === 'checking' || phase === 'sending') {
    return (
      <View style={s.panel} testID="pending-checking">
        <ActivityIndicator />
        <Text style={s.body}>{t('tasks.pending.checking')}</Text>
      </View>
    );
  }

  const notFound = phase === 'not_found';
  const words =
    phase === 'not_found' ? 'not_found' : phase === 'unavailable' ? 'unavailable' : 'unknown';
  return (
    <View style={s.panel} testID={`pending-${words.replace('_', '-')}`}>
      <Text style={s.title}>{t(`tasks.pending.${words}_title`)}</Text>
      <Text style={s.body}>{t(`tasks.pending.${words}_body`)}</Text>
      <View style={s.saved}>
        <Text style={s.savedTitle}>{pending.title}</Text>
        <Text style={s.savedMeta}>
          {[
            pending.assignee_name ?? t('tasks.assignee.unassigned'),
            pending.due_date ? formatDateAU(pending.due_date) : t('tasks.due.none'),
          ].join(' · ')}
        </Text>
      </View>
      {notFound ? (
        <PrimaryButton
          label={t('tasks.pending.send_again')}
          onPress={() => void sendAgain()}
          testID="pending-send-again"
        />
      ) : (
        <PrimaryButton
          label={t('tasks.pending.check_again')}
          onPress={() => void check()}
          testID="pending-check-again"
        />
      )}
      <Pressable onPress={discard} accessibilityRole="button" testID="pending-discard" hitSlop={8}>
        <Text style={s.discard}>{t('tasks.pending.discard')}</Text>
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  panel: {
    gap: 10,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
  },
  title: { color: tokens.ink, fontWeight: '700', fontSize: 16 },
  body: { color: tokens.warnMid },
  saved: {
    backgroundColor: tokens.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: tokens.line,
    padding: 10,
    gap: 4,
  },
  savedTitle: { color: tokens.ink, fontWeight: '600' },
  savedMeta: { color: tokens.ink3, fontSize: 12 },
  discard: { color: tokens.bad, fontWeight: '600', textAlign: 'center', marginTop: 4 },
});
