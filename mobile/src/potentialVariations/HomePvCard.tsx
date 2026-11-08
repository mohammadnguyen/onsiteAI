import { Pressable, StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';

import {
  PV_PAGE_MAX,
  listPotentialVariations,
} from '../api/potentialVariations';
import { useScaledStyles } from '../ui/type';
import { tokens } from '../ui/tokens';
import { cappedCount } from './status';

/**
 * Home, for the builder: how many potential variations are waiting to be
 * checked (DEC-PV-001 - a flag left in To check is the risk the founder
 * named, so the count is put where the builder looks every day).
 *
 * The caller mounts this for admins only. One capped page is asked for, so
 * a full page reads "50+". Nothing is shown when there is nothing to check,
 * or when the count could not be read (a failed or route-less request is
 * never presented as "0").
 */
export function HomePvCard() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ['potential-variations', 'count', 'to_check'],
    queryFn: () => listPotentialVariations({ review_state: 'to_check', limit: PV_PAGE_MAX }),
  });
  const count = Array.isArray(q.data) ? q.data.length : 0;
  if (q.isError || count === 0) return null;
  return (
    <Pressable
      style={({ pressed }) => [s.card, pressed && s.pressed]}
      onPress={() => router.push('/potential-variations' as never)}
      accessibilityRole="button"
      testID="home-pv-to-check"
    >
      <Text style={s.text} numberOfLines={2}>
        {t('potentialVariations.home.to_check', { n: cappedCount(count, PV_PAGE_MAX) })}
      </Text>
      <Text style={s.chevron}>{'›'}</Text>
    </Pressable>
  );
}

const base = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: tokens.warnBg,
    borderWidth: 1,
    borderColor: tokens.warnBorder,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  pressed: { opacity: 0.8 },
  text: { flex: 1, fontSize: 14.5, fontWeight: '600', color: tokens.warn },
  chevron: { fontSize: 18, color: tokens.warnMid },
});
