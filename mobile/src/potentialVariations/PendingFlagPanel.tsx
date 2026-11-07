import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';

import { confirmDestructive } from '../siteLog/dialogs';
import { useAuthStore } from '../store/auth';
import type { PendingPvCreate } from '../store/pvPendingCreates';
import { PrimaryButton } from '../ui/kit';
import { tokens } from '../ui/tokens';
import {
  discardPvPending,
  resolvePvPending,
  sendPvCreate,
  type PvRefusal,
} from './createFlow';

type Phase = 'checking' | 'not_found' | 'unknown' | 'unavailable' | 'sending';

/**
 * Every await below can outlive the screen or the session it began in: is it
 * still the same account (then what the server said is true, and the lists
 * must hear of it), and is the user still on this screen (then, and only
 * then, move them or change what the panel says).
 */
function sameSession(startedUnder: number): boolean {
  return useAuthStore.getState().sessionNonce === startedUnder;
}

/**
 * An earlier flag from this entry whose answer never came - the task
 * screen's panel (src/tasks/PendingCreatePanel.tsx), for a flag.
 *
 * On opening, what happened to it is found out first: if this app still has
 * it out, its own answer is awaited; otherwise the server is ASKED. Found:
 * the flag opens and says it was already saved. Not found: the saved values
 * are shown with "Send again", which reuses the same key and the same
 * values, so even a request that was only slow cannot become a second flag.
 * Still unreachable: it says the result is unknown and offers to check
 * again. The server offers no flags right now: it says so, keeps the record
 * and offers to check again - never to send. Discard asks first.
 */
export function PendingFlagPanel({
  pending,
  screenActive,
  onRefused,
  onDiscarded,
}: {
  pending: PendingPvCreate;
  /** The SCREEN's focus: resolving a record unmounts this panel first. */
  screenActive: { readonly current: boolean };
  /** The server refused on purpose; the record is gone, and its values are
   *  handed back so the user can check and save them again. */
  onRefused: (refusal: PvRefusal, values: PendingPvCreate) => void;
  /** The user discarded the record and it is gone from the phone. */
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
    const outcome = await resolvePvPending(pending, startedUnder);
    if (outcome.kind === 'stale' || !sameSession(startedUnder)) return;
    if (outcome.kind === 'found') {
      void qc.invalidateQueries({ queryKey: ['potential-variations'] });
      if (activeRef.current) {
        router.replace(
          `/potential-variations/${outcome.pv.potential_variation_id}?notice=already_created` as never,
        );
      }
      return;
    }
    if (outcome.kind === 'refused') {
      // The earlier send was still out and the server refused it.
      onRefused(outcome, pending);
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
    const outcome = await sendPvCreate(pending, startedUnder);
    if (outcome.kind === 'stale' || !sameSession(startedUnder)) return;
    if (outcome.kind === 'created') {
      void qc.invalidateQueries({ queryKey: ['potential-variations'] });
      if (activeRef.current) {
        router.replace(`/potential-variations/${outcome.pv.potential_variation_id}` as never);
      }
    } else if (outcome.kind === 'refused') {
      onRefused(outcome, pending);
    } else if (outcome.kind === 'not_saved') {
      // Nothing was sent; the record is as it was.
      setPhase('not_found');
    } else if (outcome.kind === 'unavailable') {
      setPhase('unavailable');
    } else {
      setPhase('unknown');
    }
  };

  const discard = () => {
    const startedUnder = useAuthStore.getState().sessionNonce;
    confirmDestructive({
      title: t('potentialVariations.pending.discard_title'),
      body: t('potentialVariations.pending.discard_body'),
      confirmLabel: t('potentialVariations.pending.discard'),
      cancelLabel: t('common.cancel'),
      onConfirm: () => {
        if (useAuthStore.getState().sessionNonce !== startedUnder) return;
        void discardPvPending(pending.create_client_id).then((gone) => {
          // Only forgets the record on this phone: a request that reached
          // the server is not cancelled (the dialog says so).
          if (gone && sameSession(startedUnder)) onDiscarded();
        });
      },
    });
  };

  if (phase === 'checking' || phase === 'sending') {
    return (
      <View style={s.panel} testID="pv-pending-checking">
        <ActivityIndicator />
        <Text style={s.body}>{t('potentialVariations.pending.checking')}</Text>
      </View>
    );
  }

  const words = phase === 'not_found' ? 'not_found' : phase === 'unavailable' ? 'unavailable' : 'unknown';
  return (
    <View style={s.panel} testID={`pv-pending-${words.replace('_', '-')}`}>
      <Text style={s.title}>{t(`potentialVariations.pending.${words}_title`)}</Text>
      <Text style={s.body}>{t(`potentialVariations.pending.${words}_body`)}</Text>
      <View style={s.saved}>
        <Text style={s.savedTitle}>{pending.title}</Text>
        <Text style={s.savedMeta}>
          {t('potentialVariations.new.version', { n: pending.source_revision_no })}
        </Text>
      </View>
      {phase === 'not_found' ? (
        <PrimaryButton
          label={t('potentialVariations.pending.send_again')}
          onPress={() => void sendAgain()}
          testID="pv-pending-send-again"
        />
      ) : (
        <PrimaryButton
          label={t('potentialVariations.pending.check_again')}
          onPress={() => void check()}
          testID="pv-pending-check-again"
        />
      )}
      <Pressable onPress={discard} accessibilityRole="button" testID="pv-pending-discard" hitSlop={8}>
        <Text style={s.discard}>{t('potentialVariations.pending.discard')}</Text>
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
