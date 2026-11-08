import axios from 'axios';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';

import { useMe } from '../api/hooks/useAuth';
import { listEventPotentialVariations, type PvOut } from '../api/potentialVariations';
import { useAuthStore } from '../store/auth';
import { usePvPendingCreates } from '../store/pvPendingCreates';
import { StatusBadge } from '../ui/kit';
import { tokens } from '../ui/tokens';
import { pvStateBadgeKey, visibleTitle } from './status';

/**
 * "Potential variations from this entry", on a Site Log entry's screen
 * (DEC-PV-001) - the Tasks section's shape (src/tasks/EntryTasksSection).
 *
 * Lists the flags made from this entry that this user may see (their own;
 * the builder sees all), offers "Flag as potential variation" (not on a
 * withdrawn entry, not while a flag from here is unresolved), and shows a
 * flag from here whose result is still unknown. Nothing here changes the
 * entry, and nothing here touches its tasks.
 */
export function EntryPvSection({ eventId, withdrawn }: { eventId: string; withdrawn: boolean }) {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const tokenUserId = useAuthStore((s) => s.userId);
  const userId = me?.user_id ?? tokenUserId;
  const pending = usePvPendingCreates((s) => (userId ? s.forEvent(userId, eventId) : undefined));
  const q = useQuery({
    queryKey: ['potential-variations', 'event', eventId],
    queryFn: () => listEventPotentialVariations(eventId),
  });
  const refused = q.isError && axios.isAxiosError(q.error) && q.error.response?.status === 404;

  // A server without flags answers 404 on this route (as does one that no
  // longer lets this user read the entry). A flag from here still in doubt
  // stays on the phone, and the screen says so.
  if (refused) {
    return (
      <View style={s.section} testID="entry-pv-unavailable">
        <Text style={s.note}>{t('potentialVariations.unavailable')}</Text>
        {pending ? (
          <Text style={s.note} testID="entry-pv-pending-kept">
            {t('potentialVariations.pending.kept_unavailable')}
          </Text>
        ) : null}
      </View>
    );
  }

  // Rows only from a read that has just succeeded. The query cache keeps an
  // earlier read's rows through a failed one - a 404 (this user may no
  // longer read the entry) and then a read that merely failed would bring
  // them back, and their titles come from the entry's text. And only a list
  // is a list: anything else (a proxy's page) shows no rows.
  const listed: PvOut[] | null = q.isSuccess && Array.isArray(q.data) ? q.data : null;
  const flags: PvOut[] = listed ?? [];
  const openFlag = () => router.push(`/potential-variations/new?eventId=${eventId}` as never);

  return (
    <View style={s.section} testID="entry-pv">
      <Text style={s.title}>{t('potentialVariations.entry_section.title')}</Text>
      {pending ? (
        <Pressable
          style={s.pending}
          onPress={openFlag}
          accessibilityRole="button"
          testID="entry-pv-pending"
        >
          <Text style={s.pendingText}>{t('potentialVariations.pending.banner')}</Text>
        </Pressable>
      ) : null}
      {q.isLoading ? <ActivityIndicator /> : null}
      {q.isError ? (
        <Text style={s.note}>{t('potentialVariations.entry_section.load_failed')}</Text>
      ) : null}
      {listed !== null && listed.length === 0 ? (
        <Text style={s.note}>{t('potentialVariations.entry_section.none')}</Text>
      ) : null}
      {flags.map((pv) => (
        <Pressable
          key={pv.potential_variation_id}
          style={s.row}
          onPress={() => router.push(`/potential-variations/${pv.potential_variation_id}` as never)}
          accessibilityRole="button"
          testID={`entry-pv:${pv.potential_variation_id}`}
        >
          <Text style={s.rowText} numberOfLines={2}>
            {visibleTitle(pv) ?? t('potentialVariations.placeholder_title')}
          </Text>
          <StatusBadge
            status={pvStateBadgeKey(pv.review_state)}
            label={t(`potentialVariations.state.${pv.review_state}`)}
          />
        </Pressable>
      ))}
      {!withdrawn && !pending ? (
        <Pressable
          style={s.flag}
          onPress={openFlag}
          accessibilityRole="button"
          testID="entry-pv-flag"
        >
          <Text style={s.flagText}>{t('potentialVariations.entry_section.flag')}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  section: { marginTop: 12, gap: 8 },
  title: { color: tokens.ink, fontWeight: '700', fontSize: 16 },
  note: { color: tokens.ink3, fontSize: 13 },
  pending: {
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  pendingText: { color: tokens.warnMid, fontWeight: '600' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
  },
  rowText: { flex: 1, color: tokens.ink },
  flag: {
    alignSelf: 'flex-start',
    borderWidth: 1,
    borderColor: tokens.selBorder,
    backgroundColor: tokens.sel,
    borderRadius: 999,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  flagText: { color: tokens.selText, fontWeight: '600' },
});
