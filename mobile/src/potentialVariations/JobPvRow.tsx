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
 * A job's way to its potential variations (DEC-PV-001: a flag is found
 * again by job). The count is of the flags on this job that this user may
 * see - every one for the builder, their own for anyone else - in any
 * state, so the list it opens shows every state too. One capped page is
 * asked for: a full page reads "50+". Hidden when the count could not be
 * read, so a server without flags shows nothing rather than "0".
 */
export function JobPvRow({ jobId }: { jobId: string }) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ['potential-variations', 'count', 'job', jobId],
    queryFn: () => listPotentialVariations({ job_id: jobId, limit: PV_PAGE_MAX }),
  });
  if (q.isError || !Array.isArray(q.data)) return null;
  return (
    <Pressable
      style={({ pressed }) => [s.row, pressed && s.pressed]}
      onPress={() => router.push(`/potential-variations?jobId=${jobId}&state=all` as never)}
      accessibilityRole="button"
      testID="job-pv-row"
    >
      <Text style={s.label}>{t('potentialVariations.job_row.title')}</Text>
      <Text style={s.count} testID="job-pv-count">
        {cappedCount(q.data.length, PV_PAGE_MAX)}
      </Text>
      <Text style={s.chevron}>{'›'}</Text>
    </Pressable>
  );
}

const base = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  pressed: { opacity: 0.8 },
  label: { flex: 1, fontSize: 14.5, fontWeight: '600', color: tokens.ink },
  count: { fontSize: 14.5, fontWeight: '700', color: tokens.ink2, fontVariant: ['tabular-nums'] },
  chevron: { fontSize: 18, color: tokens.ink3 },
});
