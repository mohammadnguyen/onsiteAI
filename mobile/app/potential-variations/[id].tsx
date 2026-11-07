import { useState } from 'react';
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
import { useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';

import { resolveApiErrorMessage } from '../../src/api/errors';
import {
  getPotentialVariation,
  updatePotentialVariation,
  type PvDetailOut,
  type PvPatch,
} from '../../src/api/potentialVariations';
import { DecisionPanel } from '../../src/potentialVariations/DecisionPanel';
import { PvEvidence } from '../../src/potentialVariations/PvEvidence';
import { PvJobSection } from '../../src/potentialVariations/PvJobSection';
import { pvStateBadgeKey, visibleTitle } from '../../src/potentialVariations/status';
import { BackLink } from '../../src/siteLog/BackLink';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { useAuthStore } from '../../src/store/auth';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/**
 * One potential variation (DEC-PV-001): its state, the entry version it was
 * flagged from, its job, and - for the builder - the decision.
 *
 * What this user may see and do comes from the server (`permissions`, and a
 * title and source block that are empty when the entry is not readable);
 * the screen only shows or hides. A flag is not client approval, a price or
 * approval to start work, and the screen says so.
 */
export default function PotentialVariationDetail() {
  const { t } = useTranslation();
  const { id, notice } = useLocalSearchParams<{ id: string; notice?: string }>();
  const qc = useQueryClient();
  const activeRef = useScreenActive();
  const detailKey = ['potential-variations', 'detail', id];
  const q = useQuery({
    queryKey: detailKey,
    queryFn: () => getPotentialVariation(String(id)),
    enabled: Boolean(id),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** One change; true once the server has it. */
  const apply = async (patch: PvPatch): Promise<boolean> => {
    if (saving) return false;
    const startedUnder = useAuthStore.getState().sessionNonce;
    setSaving(true);
    setError(null);
    try {
      const updated = await updatePotentialVariation(String(id), patch);
      // Same account: the answer is true and every list must hear of it,
      // whether or not the user is still on this screen.
      if (useAuthStore.getState().sessionNonce !== startedUnder) return false;
      // A read that left before the change landed would put the old values
      // back: cancel it first, then show the server's answer.
      await qc.cancelQueries({ queryKey: detailKey, exact: true });
      if (useAuthStore.getState().sessionNonce !== startedUnder) return false;
      qc.setQueryData<PvDetailOut>(detailKey, updated);
      // Read again after every save, so answers that arrive out of order
      // end on what the server holds now (the task screen's rule).
      void qc.invalidateQueries({ queryKey: detailKey, exact: true });
      void qc.invalidateQueries({ queryKey: ['potential-variations', 'list'] });
      void qc.invalidateQueries({ queryKey: ['potential-variations', 'event'] });
      void qc.invalidateQueries({ queryKey: ['potential-variations', 'count'] });
      return true;
    } catch (err) {
      if (useAuthStore.getState().sessionNonce !== startedUnder || !activeRef.current) return false;
      setError(resolveApiErrorMessage(err, t, t('potentialVariations.detail.save_failed')));
      return false;
    } finally {
      setSaving(false);
    }
  };

  if (q.isLoading) return <ActivityIndicator style={s.spinner} />;
  if (!q.data) {
    const missing = axios.isAxiosError(q.error) && q.error.response?.status === 404;
    return (
      <SafeAreaView style={s.safe} edges={['top']}>
        <View style={s.body}>
          <BackLink fallback="/potential-variations" />
          <Text style={s.note}>
            {missing ? t('potentialVariations.detail.not_found') : t('potentialVariations.list.load_failed')}
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  const pv = q.data;
  const p = pv.permissions;
  const dateOf = (iso: string) => new Date(iso).toLocaleDateString();
  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <KeyboardAvoidingView style={s.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
          <BackLink fallback="/potential-variations" />
          {notice === 'already_created' ? (
            <Text style={s.notice} testID="pv-already-created">
              {t('potentialVariations.pending.found')}
            </Text>
          ) : null}
          <Text style={s.h1} testID="pv-title-text">
            {visibleTitle(pv) ?? t('potentialVariations.placeholder_title')}
          </Text>
          <View style={s.stateRow}>
            <StatusBadge
              status={pvStateBadgeKey(pv.review_state)}
              label={t(`potentialVariations.state.${pv.review_state}`)}
              testID="pv-state"
            />
            {pv.review_state === 'ended' && pv.ended_reason ? (
              <Text style={s.reason} testID="pv-ended-reason">
                {t(`potentialVariations.reason.${pv.ended_reason}`)}
              </Text>
            ) : null}
          </View>
          <Text style={s.disclaimer} testID="pv-disclaimer">
            {t('potentialVariations.disclaimer')}
          </Text>

          {/* The last read failed, so what is shown may be older than what
              the server holds. Say so; offer a read only. */}
          {q.isRefetchError ? (
            <View style={s.stale} testID="pv-not-refreshed">
              <Text style={s.staleText}>{t('potentialVariations.detail.not_refreshed')}</Text>
              <Pressable
                onPress={() => void q.refetch()}
                disabled={q.isFetching}
                accessibilityRole="button"
                hitSlop={8}
                testID="pv-refresh"
              >
                <Text style={[s.link, q.isFetching ? s.dim : null]}>
                  {t('potentialVariations.detail.refresh')}
                </Text>
              </Pressable>
            </View>
          ) : null}

          <Text style={s.label}>{t('potentialVariations.detail.source')}</Text>
          <PvEvidence pv={pv} />

          <Text style={s.label}>{t('potentialVariations.detail.job')}</Text>
          <PvJobSection pv={pv} disabled={saving} onSetJob={(jobId) => void apply({ job_id: jobId })} />

          {p.read_note && pv.decision_note ? (
            <>
              <Text style={s.label}>{t('potentialVariations.detail.note')}</Text>
              <Text style={s.value} testID="pv-note">
                {pv.decision_note}
              </Text>
            </>
          ) : null}
          {pv.decided_by && pv.decided_at ? (
            <Text style={s.meta} testID="pv-decided-by">
              {t('potentialVariations.detail.decided_by', {
                name: pv.decided_by.full_name,
                date: dateOf(pv.decided_at),
              })}
            </Text>
          ) : null}

          {p.decide ? (
            <>
              <Text style={s.label}>{t('potentialVariations.decision.title')}</Text>
              <DecisionPanel pv={pv} disabled={saving} onDecide={(decision) => apply({ decision })} />
            </>
          ) : null}

          {saving ? <ActivityIndicator /> : null}
          {error ? (
            <Text style={s.error} testID="pv-save-error">
              {error}
            </Text>
          ) : null}
          <Text style={s.meta}>
            {t('potentialVariations.flagged_by', {
              name: pv.created_by?.full_name ?? '—',
              date: dateOf(pv.created_at),
            })}
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  flex: { flex: 1 },
  body: { padding: 16, gap: 10 },
  spinner: { marginTop: 48 },
  notice: {
    color: tokens.ok,
    backgroundColor: tokens.okBg,
    borderColor: tokens.okBorder,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    fontWeight: '600',
  },
  h1: { fontSize: 21, fontWeight: '700', color: tokens.ink },
  stateRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  reason: { color: tokens.ink2, fontSize: 13, fontWeight: '600' },
  disclaimer: {
    color: tokens.ink2,
    backgroundColor: tokens.surfaceSub,
    borderColor: tokens.line,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    fontSize: 13,
  },
  stale: {
    gap: 6,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
  },
  staleText: { color: tokens.warnMid },
  label: { color: tokens.ink2, fontWeight: '600', marginTop: 8 },
  value: { color: tokens.ink, fontSize: 15 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  dim: { opacity: 0.4 },
  note: { color: tokens.ink3, marginTop: 16 },
  error: { color: tokens.bad },
  meta: { color: tokens.ink3, fontSize: 12, marginTop: 4 },
});
